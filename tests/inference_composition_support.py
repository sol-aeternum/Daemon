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
        factories = [
            n
            for n in tree.body
            if isinstance(n, ast.AsyncFunctionDef)
            and n.name in ("qualify_task_composition", "qualify_worker_composition")
        ]
        factory_nodes = (
            {id(node) for factory in factories for node in ast.walk(factory)}
            if path == "orchestrator/tasks/composition.py"
            else set()
        )
        host_function = {
            "orchestrator/db.py": "init_app_state",
            "orchestrator/worker/worker.py": "on_startup",
        }.get(path)
        host_nodes = {
            id(node)
            for function in ast.walk(tree)
            if isinstance(function, ast.AsyncFunctionDef) and function.name == host_function
            for node in ast.walk(function)
        }

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
                        "history_observation",
                        "history_runtime",
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
                        host_binding = (
                            path == "orchestrator/db.py"
                            and id(node) in host_nodes
                            and ast.unparse(target) == "state.task_composition"
                            and isinstance(node.value, ast.Name)
                            and node.value.id == "bundle"
                        )
                        read_host_binding = (
                            path == "orchestrator/db.py"
                            and id(node) in host_nodes
                            and node.value is not None
                            and (
                                (
                                    ast.unparse(target) == "state.history_runtime"
                                    and ast.unparse(node.value)
                                    == "HistoryRuntime(history_config, settings)"
                                )
                                or (
                                    ast.unparse(target) == "state.history_observation"
                                    and ast.unparse(node.value) == "state.history_runtime.reader"
                                )
                            )
                        )
                        if (
                            not default
                            and not factory_binding
                            and not host_binding
                            and not read_host_binding
                        ):
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
                                    "orchestrator.services.restore_history",
                                    "InferenceRecovery",
                                    "TaskStore",
                                    "HistoryAcceptance",
                                    "HistoryEffects",
                                    "HistoryObservation",
                                    "HistoryProjection",
                                    "ArtifactHistoryProjection",
                                    "TerminalHistoryProjection",
                                    "ReservedOutputPublisher",
                                    "OutputReservations",
                                    "NativeProfile",
                                    "NativeContext",
                                    "NativeBudgets",
                                    "register_output_job",
                                    "reserve_output_intent",
                                    "_reserved_transaction",
                                    "_reserved_snapshot",
                                    "_publish_reserved",
                                    "_present_reserved",
                                    "_presentation_snapshot",
                                    "_preparation_snapshot",
                                    "encode_csv",
                                    "_presentation_references",
                                    "_reserved_namespace",
                                    "_owned",
                                    "_accept_with_stage",
                                    "_mark_with_stage",
                                    "_request_cancel_with_stage",
                                    "_accept_once",
                                    "retain_pool_cleanup",
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
            if isinstance(node, ast.Call):
                target = name(node.func)
                if any(
                    target == n or target.endswith("." + n)
                    for n in (
                        "NativeProfile",
                        "NativeContext",
                        "NativeBudgets",
                        "admit_native",
                        "handle_native",
                    )
                ):
                    violations.append(
                        f"{path}:{node.lineno}:uninstalled native publication profile"
                    )
                if target.endswith("ReservedOutputPublisher"):
                    violations.append(
                        f"{path}:{node.lineno}:unapproved output publisher construction"
                    )
                if (
                    target.endswith("OutputReservations")
                    and path == "orchestrator/services/restore_history/effects.py"
                ):
                    violations.append(f"{path}:{node.lineno}:unapproved reservation construction")
                if target == "encode_csv" or target.endswith(".encode_csv"):
                    functions = [
                        f
                        for f in nodes
                        if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and (
                            isinstance(f, ast.FunctionDef)
                            if path == "orchestrator/services/restore_history/native_publication.py"
                            else isinstance(f, ast.AsyncFunctionDef)
                        )
                        and f.lineno <= node.lineno <= (f.end_lineno or f.lineno)
                    ]
                    method = max(functions, key=lambda f: f.lineno).name if functions else ""
                    expected = {
                        ("orchestrator/resource_csv.py", "publish"): "self.byte_limit",
                        (
                            "orchestrator/services/restore_history/effects.py",
                            "prepare_output",
                        ): "8388608",
                        (
                            "orchestrator/services/restore_history/native_publication.py",
                            "input",
                        ): "budgets.csv_bytes",
                    }.get((path, method))
                    if not (
                        expected is not None
                        and ast.unparse(node.func) == "encode_csv"
                        and [ast.unparse(a) for a in node.args] == ["rows"]
                        and len(node.keywords) == 1
                        and node.keywords[0].arg == "byte_limit"
                        and ast.unparse(node.keywords[0].value) == expected
                    ):
                        violations.append(f"{path}:{node.lineno}:unapproved CSV encoder seam")
                if any(
                    target == n or target.endswith("." + n)
                    for n in (
                        "_reserved_transaction",
                        "_reserved_snapshot",
                        "_publish_reserved",
                        "_present_reserved",
                        "_presentation_snapshot",
                        "_preparation_snapshot",
                        "_presentation_references",
                        "_reserved_namespace",
                        "register_output_job",
                        "reserve_output_intent",
                    )
                ):
                    enclosing = [
                        f
                        for f in nodes
                        if isinstance(f, ast.AsyncFunctionDef)
                        and f.lineno <= node.lineno <= (f.end_lineno or f.lineno)
                    ]
                    method = max(enclosing, key=lambda f: f.lineno).name if enclosing else None
                    expression = ast.unparse(node.func)
                    args = [ast.unparse(arg) for arg in node.args]
                    h_calls: dict[str | None, dict[str, list[str]]] = {
                        "_prepare_output": {
                            "publisher._reserved_transaction": [],
                            "publisher._preparation_snapshot": ["conn", "identity"],
                        },
                        "_reserve_output": {
                            "publisher._reserved_transaction": [],
                            "publisher._presentation_snapshot": ["conn", "identity"],
                            "reservations.register_output_job": ["conn", "identity", "command.job"],
                            "reservations.reserve_output_intent": ["conn", "identity", "command"],
                        },
                        "_publish_reserved_output": {
                            "publisher._reserved_transaction": [],
                            "publisher._reserved_snapshot": ["conn", "identity", "intent_id"],
                            "publisher._publish_reserved": [
                                "conn",
                                "identity",
                                "intent_id",
                                "content",
                            ],
                        },
                        "_present_reserved_output": {
                            "publisher._reserved_transaction": [],
                            "publisher._reserved_snapshot": ["conn", "identity", "intent_id"],
                            "publisher._present_reserved": ["conn", "identity", "intent_id"],
                        },
                    }
                    expected_h = h_calls.get(method, {})
                    h = (
                        path == "orchestrator/services/restore_history/effects.py"
                        and expression in expected_h
                        and args == expected_h[expression]
                        and not node.keywords
                    )
                    expected_native: dict[tuple[str | None, str], list[str]] = {
                        ("_publish_reserved", "self._reserved_snapshot"): [
                            "conn",
                            "identity",
                            "intent",
                        ],
                        ("_present_reserved", "self._reserved_snapshot"): [
                            "conn",
                            "identity",
                            "intent",
                        ],
                        ("_publish_reserved", "self._reserved_namespace"): [
                            "conn",
                            "identity",
                            "observed",
                            "stored",
                        ],
                        ("_present_reserved", "self._reserved_namespace"): [
                            "conn",
                            "identity",
                            "observed",
                            "stored",
                        ],
                        ("_present_reserved", "self._presentation_snapshot"): ["conn", "identity"],
                        ("_present_reserved", "self._presentation_references"): [
                            "conn",
                            "identity",
                            "pins",
                            "task",
                        ],
                    }
                    admit = (
                        method == "_publish_reserved"
                        and expression == "self._reserved_snapshot"
                        and len(node.keywords) == 1
                        and node.keywords[0].arg == "admit"
                        and isinstance(node.keywords[0].value, ast.Constant)
                        and node.keywords[0].value.value is True
                    )
                    native = (
                        path == "orchestrator/resource_store.py"
                        and (method, expression) in expected_native
                        and args == expected_native[(method, expression)]
                        and (not node.keywords or admit)
                    )
                    legacy_r0 = (
                        path == "orchestrator/resource_csv.py"
                        and method == "publish"
                        and not node.keywords
                        and (expression, args)
                        in (
                            ("self.r0.register_output_job", ["conn", "identity", "job"]),
                            ("self.r0.reserve_output_intent", ["conn", "identity", "command"]),
                        )
                    )
                    if not (h or native or legacy_r0):
                        violations.append(f"{path}:{node.lineno}:unapproved reserved output seam")
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
                    == {"InferenceRecovery", "acceptor_preflight", "acceptor_catalog_preflight"}
                )
                history_helpers = (
                    path == "orchestrator/services/restore_history/acceptance.py"
                    and isinstance(node, ast.ImportFrom)
                    and node.level == 0
                    and node.module == "orchestrator.tasks.inference"
                    and {item.name for item in node.names}
                    == {"ROLE_PRECONDITION", "acceptor_preflight", "check_principal"}
                    and all(item.asname is None for item in node.names)
                )
                history_effect_type = (
                    path == "orchestrator/services/restore_history/effects.py"
                    and isinstance(node, ast.ImportFrom)
                    and node.level == 0
                    and node.module == "orchestrator.tasks.inference"
                    and [(item.name, item.asname) for item in node.names]
                    == [("InferenceRecovery", None)]
                )
                if isinstance(node, ast.ImportFrom) and node.level:
                    package = path.removesuffix(".py").split("/")[:-1]
                    prefix = ".".join(package[: len(package) - node.level + 1])
                    modules = [prefix + ("." + node.module if node.module else "")]
                if (
                    any("orchestrator.tasks.inference" in m for m in modules)
                    and path not in ADMITTED
                    and not notice_only
                    and not factory_import
                    and not history_helpers
                    and not history_effect_type
                ):
                    violations.append(f"{path}:{node.lineno}:unapproved importer")
                # H1 remains an uncalled source slice. Internal package imports
                # compose its definitions, never admit an external runtime host.
                if not path.startswith("orchestrator/services/restore_history/"):
                    observation_reader = (
                        path == "orchestrator/routes/tasks.py"
                        and isinstance(node, ast.ImportFrom)
                        and node.level == 0
                        and node.module == "orchestrator.services.restore_history.observation"
                        and [(item.name, item.asname) for item in node.names]
                        == [("HistoryObservation", None)]
                    )
                    startup_imports = {
                        ("orchestrator/db.py", "configuration"): {"configured_history"},
                        ("orchestrator/db.py", "model"): {"HistoryError"},
                        ("orchestrator/db.py", "runtime"): {
                            "HistoryRuntime",
                            "physical_completion",
                            "require_unselected_history",
                            "startup_deadline",
                        },
                        ("orchestrator/worker/worker.py", "configuration"): {"configured_history"},
                        ("orchestrator/worker/worker.py", "model"): {"HistoryError"},
                        ("orchestrator/worker/worker.py", "runtime"): {
                            "HistoryRuntime",
                            "physical_completion",
                            "require_unselected_history",
                            "startup_deadline",
                        },
                        ("orchestrator/main.py", "ingress"): {"HistoryObservationOnlyMiddleware"},
                        ("orchestrator/routes/tasks.py", "read_supervisor"): {
                            "HistoryReadSupervisor"
                        },
                    }
                    startup_read_only = (
                        isinstance(node, ast.ImportFrom)
                        and node.level == 0
                        and (node.module or "").startswith("orchestrator.services.restore_history.")
                        and {item.name for item in node.names}
                        == startup_imports.get((path, (node.module or "").rsplit(".", 1)[-1]))
                        and all(item.asname is None for item in node.names)
                    )
                    if isinstance(node, ast.ImportFrom):
                        module = modules[0]
                        imported = [module + "." + item.name for item in node.names]
                    else:
                        imported = modules
                    if (
                        not observation_reader
                        and not startup_read_only
                        and any(
                            m == "orchestrator.services.restore_history"
                            or m.startswith("orchestrator.services.restore_history.")
                            for m in imported
                        )
                    ):
                        violations.append(f"{path}:{node.lineno}:uncalled history importer")
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
            if target.endswith(("._stage_completed_body", "._completion_control")) or (
                target.endswith("._worker") and path != "orchestrator/tasks/store.py"
            ):
                enclosing_functions = [
                    f
                    for f in nodes
                    if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and f.lineno <= node.lineno <= (f.end_lineno or f.lineno)
                ]
                enclosing_terminal = (
                    max(enclosing_functions, key=lambda f: f.lineno).name
                    if enclosing_functions
                    else ""
                )
                expected_terminal = {
                    ("orchestrator/tasks/store.py", "_complete"): "self",
                    (
                        "orchestrator/services/restore_history/effects.py",
                        "_complete",
                    ): "self._tasks",
                }.get((path, enclosing_terminal))
                if expected_terminal is None or target.rsplit(".", 1)[0] != expected_terminal:
                    violations.append(f"{path}:{node.lineno}:unapproved terminal transaction seam")
            if target.endswith(".retain_pool_cleanup") or target == "retain_pool_cleanup":
                functions = [
                    f
                    for f in nodes
                    if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and f.lineno <= node.lineno <= (f.end_lineno or f.lineno)
                ]
                enclosing = max(functions, key=lambda f: f.lineno).name if functions else None
                if (
                    (path, enclosing)
                    not in {
                        ("orchestrator/services/restore_history/composition.py", "_accept"),
                        ("orchestrator/services/restore_history/observation.py", "_lookup"),
                        ("orchestrator/services/restore_history/effects.py", "_mark"),
                        ("orchestrator/services/restore_history/effects.py", "_cancel"),
                        ("orchestrator/services/restore_history/effects.py", "_reserve_output"),
                        ("orchestrator/services/restore_history/effects.py", "_prepare_output"),
                        ("orchestrator/services/restore_history/effects.py", "_complete"),
                        ("orchestrator/services/restore_history/effects.py", "_write_partial"),
                        (
                            "orchestrator/services/restore_history/effects.py",
                            "_publish_reserved_output",
                        ),
                        (
                            "orchestrator/services/restore_history/effects.py",
                            "_present_reserved_output",
                        ),
                        (
                            "orchestrator/services/restore_history/read_supervisor.py",
                            "qualify_read_history",
                        ),
                        (
                            "orchestrator/services/restore_history/runtime.py",
                            "qualify_before_recovery",
                        ),
                    }
                    or ast.unparse(node.func) != "retain_pool_cleanup"
                    or node.args
                    or node.keywords
                ):
                    violations.append(f"{path}:{node.lineno}:unapproved cleanup scope")
            effect_seam = next(
                (
                    method
                    for method in ("_mark_with_stage", "_request_cancel_with_stage")
                    if target.endswith("." + method) or target == method
                ),
                None,
            )
            if effect_seam is not None:
                functions = [
                    f
                    for f in nodes
                    if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and f.lineno <= node.lineno <= (f.end_lineno or f.lineno)
                ]
                enclosing = max(functions, key=lambda f: f.lineno).name if functions else None
                method = "mark" if effect_seam == "_mark_with_stage" else "request_cancel"
                keywords = {k.arg: ast.unparse(k.value) for k in node.keywords}
                args = [ast.unparse(arg) for arg in node.args]
                native = (
                    path
                    == (
                        "orchestrator/tasks/inference.py"
                        if method == "mark"
                        else "orchestrator/tasks/store.py"
                    )
                    and enclosing == method
                    and ast.unparse(node.func) == "self." + effect_seam
                    and keywords == {"stage": "None"}
                    and args
                    == (
                        ["identity", "reservation", "state"]
                        if method == "mark"
                        else ["user_id", "task_id"]
                    )
                )
                joined = (
                    path == "orchestrator/services/restore_history/effects.py"
                    and enclosing == ("_mark" if method == "mark" else "_cancel")
                    and ast.unparse(node.func)
                    == (
                        "recovery._mark_with_stage"
                        if method == "mark"
                        else "self._tasks._request_cancel_with_stage"
                    )
                    and keywords == {"stage": "stage"}
                    and args
                    == (
                        ["identity", "reservation", "invocation_state"]
                        if method == "mark"
                        else ["account", "task"]
                    )
                )
                if not (native or joined):
                    violations.append(f"{path}:{node.lineno}:unapproved effect staging seam")
            if target.endswith("._owned") and any(k.arg == "stage" for k in node.keywords):
                functions = [
                    f
                    for f in nodes
                    if isinstance(f, ast.AsyncFunctionDef)
                    and f.lineno <= node.lineno <= (f.end_lineno or f.lineno)
                ]
                enclosing = max(functions, key=lambda f: f.lineno).name if functions else None
                if not (
                    path == "orchestrator/tasks/inference.py"
                    and enclosing == "_mark_with_stage"
                    and ast.unparse(node.func) == "self._owned"
                    and next(ast.unparse(k.value) for k in node.keywords if k.arg == "stage")
                    == "original_result"
                ):
                    violations.append(f"{path}:{node.lineno}:unapproved mark transaction stage")
            staged_once = (target.endswith("._accept_once") or target == "_accept_once") and any(
                k.arg == "stage" for k in node.keywords
            )
            if (
                target.endswith("._accept_with_stage")
                or target == "_accept_with_stage"
                or staged_once
            ):
                functions = [
                    f
                    for f in nodes
                    if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and f.lineno <= node.lineno <= (f.end_lineno or f.lineno)
                ]
                enclosing = max(functions, key=lambda f: f.lineno).name if functions else None
                keywords = {k.arg: ast.unparse(k.value) for k in node.keywords}
                function = ast.unparse(node.func)
                default_only = (
                    path in ("orchestrator/tasks/store.py", "orchestrator/resource_inputs.py")
                    and enclosing == "accept"
                    and function == "self._accept_with_stage"
                    and keywords.get("stage") == "None"
                    and None not in keywords
                )
                c1_join = (
                    path == "orchestrator/tasks/store.py"
                    and enclosing == "_accept_with_stage"
                    and function == "self._resource_inputs._accept_with_stage"
                    and keywords.get("stage") == "stage"
                    and None not in keywords
                )
                history_join = (
                    path == "orchestrator/services/restore_history/composition.py"
                    and enclosing == "_accept"
                    and function == "self._tasks._accept_with_stage"
                    and keywords == {"stage": "stage", None: "options"}
                )
                native_join = (
                    path == "orchestrator/tasks/store.py"
                    and enclosing == "_accept_with_stage"
                    and function == "self._accept_once"
                    and keywords.get("stage") == "stage"
                    and None not in keywords
                )
                if node.args or not (default_only or c1_join or history_join or native_join):
                    violations.append(f"{path}:{node.lineno}:unapproved history staging seam")
            if not path.startswith("orchestrator/services/restore_history/"):
                if target.endswith(
                    (
                        ".AcceptanceProjection",
                        ".HistoryCoordinator",
                        ".HistoryAcceptance",
                        ".HistoryProjection",
                        ".ArtifactHistoryProjection",
                        ".TerminalHistoryProjection",
                        ".HistoryEffects",
                        ".HistoryObservation",
                        ".HistoryReadSupervisor",
                        ".HistoryRuntime",
                        ".PacketOwner",
                        ".NativeProfile",
                        ".NativeContext",
                        ".NativeBudgets",
                    )
                ) or target in (
                    "AcceptanceProjection",
                    "HistoryCoordinator",
                    "HistoryAcceptance",
                    "HistoryProjection",
                    "ArtifactHistoryProjection",
                    "TerminalHistoryProjection",
                    "HistoryEffects",
                    "HistoryObservation",
                    "HistoryReadSupervisor",
                    "HistoryRuntime",
                    "PacketOwner",
                    "NativeProfile",
                    "NativeContext",
                    "NativeBudgets",
                ):
                    read_host = (
                        target == "orchestrator.services.restore_history.runtime.HistoryRuntime"
                        and id(node) in host_nodes
                        and (
                            (
                                path == "orchestrator/db.py"
                                and ast.unparse(node) == "HistoryRuntime(history_config, settings)"
                            )
                            or (
                                path == "orchestrator/worker/worker.py"
                                and ast.unparse(node)
                                == "HistoryRuntime(history_config, app_settings)"
                            )
                        )
                    )
                    if not read_host:
                        violations.append(f"{path}:{node.lineno}:history adapter construction")
                if (
                    target in ("importlib.import_module", "__import__")
                    and node.args
                    and "orchestrator.services.restore_history" in name(node.args[0])
                ):
                    violations.append(f"{path}:{node.lineno}:dynamic history importer")
            admitted_call = factory_constructor(node, target)
            if (
                path not in ADMITTED
                and any(
                    keyword.arg
                    in ("inference_recovery", "cover_new_inference", "history_observation")
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
            if target.endswith(
                (".qualify_task_composition", ".qualify_worker_composition")
            ) or target in ("qualify_task_composition", "qualify_worker_composition"):
                keywords = {k.arg: ast.unparse(k.value) for k in node.keywords}
                expected = {
                    "orchestrator/db.py": {
                        "enabled": "True",
                        "acceptor_pool": "state.task_acceptor_pool",
                        "worker_pool": "state.db_pool",
                        "encryption": "encryption",
                        "settings": "settings",
                        "acceptor_role": "settings.daemon_task_acceptor_role",
                        "worker_role": "settings.daemon_task_worker_role",
                    },
                    "orchestrator/worker/worker.py": {
                        "enabled": "True",
                        "worker_pool": "db_pool",
                        "encryption": "encryption",
                        "settings": "app_settings",
                        "acceptor_role": "app_settings.daemon_task_acceptor_role",
                        "worker_role": "app_settings.daemon_task_worker_role",
                    },
                }.get(path)
                required_factory = (
                    ".qualify_task_composition"
                    if path == "orchestrator/db.py"
                    else ".qualify_worker_composition"
                )
                if not (
                    id(node) in host_nodes
                    and target.endswith(required_factory)
                    and not node.args
                    and keywords == expected
                ):
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
