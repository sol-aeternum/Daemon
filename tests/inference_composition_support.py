"""Discriminating source inventory, NOT an arbitrary Python execution sandbox."""

import ast


ADMITTED = {
    "orchestrator/compute_runtime.py",
    "orchestrator/tasks/store.py",
    "orchestrator/tasks/runner.py",
    "orchestrator/tasks/inference.py",
}


def composition_violations(sources: dict[str, str]) -> list[str]:
    violations = []
    for path, source in sources.items():
        if not path.startswith(("orchestrator/", "providers/", "scripts/")):
            continue  # explicit fixture injection is intentionally not production
        tree = ast.parse(source)
        nodes = list(ast.walk(tree))
        aliases = {}

        def name(node):
            if isinstance(node, ast.Name):
                return aliases.get(node.id, node.id)
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            if isinstance(node, ast.Attribute):
                return name(node.value) + "." + node.attr
            if isinstance(node, ast.Call):
                function = name(node.func)
                if function in ("importlib.import_module", "__import__") and node.args:
                    return name(node.args[0])
                if function == "getattr" and len(node.args) >= 2:
                    return name(node.args[0]) + "." + name(node.args[1])
            return ""

        for node in nodes:
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and path not in ADMITTED:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(
                    isinstance(target, ast.Attribute)
                    and target.attr in ("_inference", "_cover_new_inference")
                    for target in targets
                ):
                    violations.append(f"{path}:{node.lineno}:direct consumer installation")
            if isinstance(node, ast.Import):
                for item in node.names:
                    aliases[item.asname or item.name.split(".")[0]] = (
                        item.name if item.asname else item.name.split(".")[0]
                    )
            elif isinstance(node, ast.ImportFrom):
                for item in node.names:
                    aliases[item.asname or item.name] = (node.module or "") + "." + item.name
        # Fixed point resolves chained local aliases regardless of ast.walk order.
        # Bounded known-symbol propagation, not general value-flow interpretation.
        # Never propagate ordinary local variables (reused names need not converge).
        for _ in range(8):
            changed = False
            for node in nodes:
                if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        value = name(node.value)
                        if (
                            isinstance(target, ast.Name)
                            and value
                            and any(
                                symbol in value
                                for symbol in (
                                    "orchestrator.tasks",
                                    "InferenceRecovery",
                                    "TaskStore",
                                    "abandoned_pass",
                                    "importlib",
                                    "__import__",
                                    "getattr",
                                )
                            )
                            and aliases.get(target.id) != value
                        ):
                            aliases[target.id] = value
                            changed = True
            if not changed:
                break
        for node in nodes:
            if isinstance(node, ast.ClassDef) and node.name == "TaskStore":
                for method in node.body:
                    if isinstance(method, ast.FunctionDef) and method.name == "__init__":
                        defaults = dict(
                            zip((a.arg for a in method.args.kwonlyargs), method.args.kw_defaults)
                        )
                        for key, expected in (
                            ("inference_recovery", None),
                            ("cover_new_inference", False),
                        ):
                            value = defaults.get(key)
                            if not isinstance(value, ast.Constant) or value.value is not expected:
                                violations.append(
                                    f"{path}:{method.lineno}:enabled or missing default {key}"
                                )
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = (
                    [i.name for i in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                # Existing observer imports ONLY the two user-facing constants.
                # This does not admit its consumer/class or module aliases.
                notice_only = (
                    path == "orchestrator/tasks/observe.py"
                    and isinstance(node, ast.ImportFrom)
                    and node.module == "orchestrator.tasks.inference"
                    and {item.name for item in node.names} <= {"CODE", "NOTICE"}
                )
                if (
                    any("orchestrator.tasks.inference" in m for m in modules)
                    and path not in ADMITTED
                    and not notice_only
                ):
                    violations.append(f"{path}:{node.lineno}:unapproved importer")
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.module == "orchestrator.tasks"
                    and any(i.name == "inference" for i in node.names)
                    and path not in ADMITTED
                ):
                    violations.append(f"{path}:{node.lineno}:unapproved importer")
            if not isinstance(node, ast.Call):
                continue
            target = name(node.func)
            if path not in ADMITTED and any(
                keyword.arg in ("inference_recovery", "cover_new_inference")
                for keyword in node.keywords
            ):
                violations.append(f"{path}:{node.lineno}:collaborator injection")
            if target.endswith(".InferenceRecovery") or target == "InferenceRecovery":
                violations.append(f"{path}:{node.lineno}:consumer construction")
            if target.endswith(".TaskStore") or target == "TaskStore":
                if (
                    len(node.args) != 3
                    or node.keywords
                    or any(isinstance(a, ast.Starred) for a in node.args)
                ):
                    violations.append(f"{path}:{node.lineno}:task composition")
            if target.endswith(".abandoned_pass"):
                violations.append(f"{path}:{node.lineno}:consumer invocation")
            if (
                target in ("importlib.import_module", "__import__")
                and node.args
                and "orchestrator.tasks.inference" in name(node.args[0])
                and path not in ADMITTED
            ):
                violations.append(f"{path}:{node.lineno}:dynamic importer")
            if (
                target == "setattr"
                and len(node.args) >= 2
                and name(node.args[1]) in ("_inference", "_cover_new_inference")
                and path not in ADMITTED
            ):
                violations.append(f"{path}:{node.lineno}:consumer installation")
    return violations
