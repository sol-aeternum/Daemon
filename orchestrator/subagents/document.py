"""@document subagent - document generation via LLM-generated Python code."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from orchestrator.skills_loader import load_document_skill
from orchestrator.subagents.base import BaseSubagent, SubagentResult, SubagentType
from orchestrator.utils import slugify_filename

logger = logging.getLogger(__name__)

# Fixed model for document generation (pinned regardless of tier)
DOCUMENT_MODEL = "anthropic/claude-sonnet-4.5"

# Directory for generated files
GENERATED_FILES_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "generated_files"


class DocumentSubagent(BaseSubagent):
    """Document generation subagent using LLM-generated Python code."""

    agent_type = SubagentType.DOCUMENT
    description = "Generates document files (docx, csv) from text descriptions using LLM-generated Python code"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize document subagent."""
        super().__init__(config)
        self.api_key = config.get("openrouter_api_key") if config else None
        self.api_key = self.api_key or os.environ.get("OPENROUTER_API_KEY")
        self.base_url = (
            (config.get("openrouter_base_url") if config else None)
            or os.environ.get("OPENROUTER_BASE_URL")
            or "https://openrouter.ai/api/v1"
        ).rstrip("/")
        self.model = DOCUMENT_MODEL
        self.timeout = 120.0

    async def execute(self, task: str, context: dict[str, Any] | None = None) -> SubagentResult:
        """Execute document generation task.

        Args:
            task: The document generation request/description
            context: Optional context (format, original_code for revisions, etc.)

        Returns:
            SubagentResult with file URL, filename, format, and generation_code
        """
        if not self.api_key:
            return self._create_result(
                success=False,
                error="OPENROUTER_API_KEY not configured",
            )

        context_payload = context or {}
        doc_format = context_payload.get("format", "docx").lower()

        # Load format-specific skill
        try:
            skill_content = load_document_skill(doc_format)
        except ValueError as e:
            return self._create_result(
                success=False,
                error=f"Unsupported format: {doc_format}. Error: {str(e)}",
            )

        # Build prompt for code generation
        system_prompt = self._build_code_generation_prompt(task, skill_content, context_payload)

        output_file: str | None = None
        cleanup_output_file = False

        try:
            # Call LLM to generate Python code
            code_result = await self._generate_code(system_prompt)

            if not code_result.get("success"):
                return self._create_result(
                    success=False,
                    error=code_result.get("error", "Code generation failed"),
                )

            generated_code = code_result.get("code", "")
            if not generated_code:
                return self._create_result(
                    success=False,
                    error="LLM returned empty code",
                )

            # Execute the generated code in sandbox
            execution_result = await self._execute_sandbox(generated_code, doc_format)

            if not execution_result.get("success"):
                return self._create_result(
                    success=False,
                    error=execution_result.get("error", "Code execution failed"),
                )

            # Get the output file path
            output_file = execution_result.get("file_path")
            cleanup_output_file = bool(execution_result.get("cleanup"))
            if not output_file or not Path(output_file).exists():
                return self._create_result(
                    success=False,
                    error="Generated file not found",
                )

            # Persist to generated_files directory
            filename_hint_value = context_payload.get("filename")
            filename_hint = filename_hint_value if isinstance(filename_hint_value, str) else ""
            persisted_path = self._persist_file(
                output_file,
                doc_format,
                filename_hint=filename_hint,
            )

            return self._create_result(
                success=True,
                data={
                    "file_url": f"/generated-files/{persisted_path.name}",
                    "filename": persisted_path.name,
                    "format": doc_format,
                    "generation_code": generated_code,
                },
                metadata={
                    "model": self.model,
                    "original_code": context_payload.get("original_code"),
                },
            )

        except Exception as e:
            return self._create_result(
                success=False,
                error=f"Document generation failed: {str(e)}",
            )
        finally:
            if cleanup_output_file and output_file:
                try:
                    Path(output_file).unlink(missing_ok=True)
                except Exception as cleanup_error:
                    logger.warning(
                        f"Failed to remove temporary generated file {output_file}: {cleanup_error}"
                    )

    def _build_code_generation_prompt(
        self, task: str, skill_content: str, context: dict[str, Any]
    ) -> str:
        """Build the prompt for code generation."""
        prompt = f"""You are an expert Python developer. Generate Python code to create a document.

USER REQUEST:
{task}

FORMAT: {context.get("format", "docx")}

"""

        # Revision flow: modify existing code
        if context.get("original_code"):
            prompt += f"""REVISION REQUEST:
The user wants to modify an existing document. Here is the original code:
```python
{context.get("original_code")}
```

Requested changes:
{context.get("change_summary", "Make the requested modifications")}

Generate the COMPLETE modified Python code. Do not use placeholders.
"""
        else:
            prompt += f"""Generate Python code using the following skill instructions:

{skill_content}

IMPORTANT:
- The code must create a file and save it using doc.save() or csv.writer
- Use ONLY stdlib and python-docx for docx format
- Use ONLY stdlib csv module for csv format
- Save the file to 'output.{context.get("format", "docx")}'
- Return ONLY the Python code, no explanations or markdown
"""

        return prompt

    async def _generate_code(self, prompt: str) -> dict[str, Any]:
        """Call LLM to generate Python code."""
        import httpx

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://daemon.ai",
            "X-Title": "Daemon AI Assistant",
        }

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are an expert Python developer. Generate complete, working Python code. Return ONLY the code without any explanations or markdown formatting.",
                },
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 4000,
            "temperature": 0.2,
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                data = response.json()

            if data.get("error"):
                return {"success": False, "error": data.get("error")}

            choices = data.get("choices") or []
            if not choices:
                return {"success": False, "error": "No response from LLM"}

            message = (choices[0] or {}).get("message") or {}
            content = message.get("content", "")

            # Extract Python code from response
            code = self._extract_code(content)

            return {"success": True, "code": code}

        except httpx.HTTPStatusError as e:
            response_text = ""
            try:
                response_text = e.response.text
            except Exception:
                response_text = ""

            status = e.response.status_code if e.response is not None else "unknown"
            detail = f"HTTP {status} from OpenRouter"
            if response_text:
                detail = f"{detail}: {response_text[:1000]}"

            return {"success": False, "error": detail}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _extract_code(self, content: str) -> str:
        """Extract Python code from LLM response."""
        # If code block present, extract it
        if "```python" in content:
            start = content.find("```python") + len("```python")
            end = content.find("```", start)
            if end > start:
                return content[start:end].strip()

        if "```" in content:
            start = content.find("```") + 3
            end = content.find("```", start)
            if end > start:
                return content[start:end].strip()

        # Return as-is if no code blocks
        return content.strip()

    async def _execute_sandbox(self, code: str, doc_format: str) -> dict[str, Any]:
        """Execute Python code in a sandboxed subprocess."""
        # Create temp directory for execution
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write code to temp file
            code_file = Path(tmpdir) / "generate.py"
            code_file.write_text(code, encoding="utf-8")

            # Output file path
            output_file = Path(tmpdir) / f"output.{doc_format}"

            try:
                # Run with timeout
                result = subprocess.run(
                    [sys.executable, str(code_file)],
                    cwd=tmpdir,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                )

                if result.returncode != 0:
                    stderr = result.stderr.strip()
                    if doc_format == "docx" and "No module named 'docx'" in stderr:
                        return {
                            "success": False,
                            "error": (
                                "Code execution failed: python-docx is not available in "
                                f"runtime interpreter ({sys.executable}). "
                                "Install dependencies for the running environment and restart "
                                "backend/worker services. "
                                f"Raw error: {stderr}"
                            ),
                        }

                    return {
                        "success": False,
                        "error": f"Code execution failed: {stderr}",
                    }

                # Check if output file was created
                if not output_file.exists():
                    return {
                        "success": False,
                        "error": (
                            "Output file not created. "
                            f"stdout: {result.stdout.strip()} stderr: {result.stderr.strip()}"
                        ),
                    }

                persisted_tmp = tempfile.NamedTemporaryFile(suffix=f".{doc_format}", delete=False)
                persisted_tmp_path = Path(persisted_tmp.name)
                persisted_tmp.close()
                shutil.copy2(output_file, persisted_tmp_path)

                return {
                    "success": True,
                    "file_path": str(persisted_tmp_path),
                    "cleanup": True,
                }

            except subprocess.TimeoutExpired:
                return {
                    "success": False,
                    "error": "Code execution timed out (30s limit)",
                }
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Execution error: {str(e)}",
                }

    def _persist_file(
        self,
        source_path: str,
        doc_format: str,
        filename_hint: str = "",
    ) -> Path:
        """Persist generated file to data/generated_files/."""
        GENERATED_FILES_DIR.mkdir(parents=True, exist_ok=True)

        safe_extension = "".join(c for c in doc_format.lower() if c.isalnum()) or "bin"
        slug = slugify_filename(filename_hint)

        if slug:
            filename = f"{slug}.{safe_extension}"
            dest_path = GENERATED_FILES_DIR / filename
            if dest_path.exists():
                suffix = uuid.uuid4().hex[:8]
                filename = f"{slug}-{suffix}.{safe_extension}"
                dest_path = GENERATED_FILES_DIR / filename
        else:
            while True:
                fallback = str(uuid.uuid4())
                filename = f"{fallback}.{safe_extension}"
                dest_path = GENERATED_FILES_DIR / filename
                if not dest_path.exists():
                    break

        shutil.copy2(source_path, dest_path)

        logger.info(f"Persisted generated document to {dest_path}")
        return dest_path
