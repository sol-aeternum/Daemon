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
        factory = next(
            (
                n
                for n in tree.body
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "qualify_task_composition"
            ),
            None,
        )
        factory_nodes = (
            set(map(id, ast.walk(factory)))
            if (path == "orchestrator/tasks/composition.py" and factory is not None)
            else set()
        )

        def factory_constructor(node, target):
            if id(node) not in factory_nodes:
                return False
            args = [ast.unparse(a) for a in node.args]
            keywords = {k.arg: ast.unparse(k.value) for k in node.keywords}
            if target.endswith("InferenceRecovery"):
                return args == ["worker_pool"] and not keywords
            if target.endswith("TaskStore"):
                return (
                    args
                    == ["acceptor_pool", "encryption", "MemoryStore(acceptor_pool, encryption)"]
                    and keywords == {"cover_new_inference": "True"}
                ) or (
                    args == ["worker_pool", "encryption", "MemoryStore(worker_pool, encryption)"]
                    and keywords == {"inference_recovery": "recovery"}
                )
            return False

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
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Attribute) and target.attr in (
                        "_composition",
                        "_task_composition",
                        "task_composition",
                    ):
                        default = (
                            path in ("orchestrator/tasks/store.py", "orchestrator/memory/store.py")
                            and isinstance(target.value, ast.Name)
                            and target.value.id == "self"
                            and isinstance(node.value, ast.Constant)
                            and node.value.value is None
                        )
                        factory_binding = (
                            id(node) in factory_nodes
                            and ast.unparse(target)
                            in (
                                "acceptor._composition",
                                "tasks._composition",
                                "acceptor._memory._task_composition",
                                "tasks._memory._task_composition",
                            )
                            and isinstance(node.value, ast.Name)
                            and node.value.id in ("bundle", "worker")
                        )
                        if not default and not factory_binding:
                            violations.append(
                                f"{path}:{node.lineno}:unapproved composition binding"
                            )
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
                factory_import = (
                    path == "orchestrator/tasks/composition.py"
                    and isinstance(node, ast.ImportFrom)
                    and node.module == "orchestrator.tasks.inference"
                    and {item.name for item in node.names}
                    == {"InferenceRecovery", "acceptor_preflight"}
                )
                if (
                    any("orchestrator.tasks.inference" in m for m in modules)
                    and path not in ADMITTED
                    and not notice_only
                    and not factory_import
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
            admitted_call = factory_constructor(node, target)
            if (
                path not in ADMITTED
                and any(
                    keyword.arg in ("inference_recovery", "cover_new_inference")
                    for keyword in node.keywords
                )
                and not admitted_call
            ):
                violations.append(f"{path}:{node.lineno}:collaborator injection")
            if (
                target.endswith(".InferenceRecovery") or target == "InferenceRecovery"
            ) and not admitted_call:
                violations.append(f"{path}:{node.lineno}:consumer construction")
            if target.endswith(".TaskStore") or target == "TaskStore":
                if not admitted_call and (
                    len(node.args) != 3
                    or node.keywords
                    or any(isinstance(a, ast.Starred) for a in node.args)
                ):
                    violations.append(f"{path}:{node.lineno}:task composition")
            if target.endswith(".qualify_task_composition") or target == "qualify_task_composition":
                violations.append(f"{path}:{node.lineno}:factory activation")
            if (
                target.endswith((".TaskComposition", ".WorkerComposition"))
                and id(node) not in factory_nodes
            ):
                violations.append(f"{path}:{node.lineno}:bundle construction")
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
                and name(node.args[1])
                in (
                    "_inference",
                    "_cover_new_inference",
                    "_composition",
                    "_task_composition",
                    "task_composition",
                )
                and path not in ADMITTED
            ):
                violations.append(f"{path}:{node.lineno}:consumer installation")
    return violations
