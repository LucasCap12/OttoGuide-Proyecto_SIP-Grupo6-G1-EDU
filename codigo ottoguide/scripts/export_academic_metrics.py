import os
import ast


def count_lines(path):
    total = 0
    code = 0
    blank = 0
    for line in path.splitlines():
        total += 1
        stripped = line.strip()
        if stripped == "":
            blank += 1
        else:
            code += 1
    return total, code, blank


def extract_fsm_states(tree):
    states = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            call = node.value
            if isinstance(call.func, ast.Name) and call.func.id == "State":
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        states.append(target.id)
        if isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Call):
            call = node.value
            if isinstance(call.func, ast.Name) and call.func.id == "State":
                if isinstance(node.target, ast.Name):
                    states.append(node.target.id)
    return states


def scan_python_files(base_dirs):
    metrics = {
        "files": 0,
        "total_lines": 0,
        "code_lines": 0,
        "blank_lines": 0,
        "fsm_states": set(),
        "per_file": []
    }

    for base in base_dirs:
        if not os.path.isdir(base):
            continue
        for root, _, files in os.walk(base):
            for name in files:
                if not name.endswith(".py"):
                    continue
                file_path = os.path.join(root, name)
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()

                total, code, blank = count_lines(content)
                metrics["files"] += 1
                metrics["total_lines"] += total
                metrics["code_lines"] += code
                metrics["blank_lines"] += blank
                metrics["per_file"].append((file_path, total, code, blank))

                try:
                    tree = ast.parse(content)
                    states = extract_fsm_states(tree)
                    for st in states:
                        metrics["fsm_states"].add(st)
                except SyntaxError:
                    pass

    metrics["per_file"].sort(key=lambda item: item[0])
    return metrics


def build_report(metrics):
    lines = []
    lines.append("OTTOGUIDE MVP - EXPORT TECNICO ACADEMICO")
    lines.append("UADE 2026")
    lines.append("")
    lines.append("RESUMEN GLOBAL")
    lines.append(f"Archivos Python analizados: {metrics['files']}")
    lines.append(f"Lineas totales: {metrics['total_lines']}")
    lines.append(f"Lineas de codigo no vacias: {metrics['code_lines']}")
    lines.append(f"Lineas en blanco: {metrics['blank_lines']}")
    lines.append("")
    lines.append("ESTADOS FSM DETECTADOS")
    if metrics["fsm_states"]:
        for state in sorted(metrics["fsm_states"]):
            lines.append(f"- {state}")
    else:
        lines.append("- No se detectaron estados FSM con patron ast.Assign(State(...))")
    lines.append("")
    lines.append("DETALLE POR ARCHIVO")
    for file_path, total, code, blank in metrics["per_file"]:
        lines.append(f"{file_path} | total={total} | codigo={code} | blanco={blank}")
    lines.append("")
    lines.append("FIN DEL REPORTE")
    return "\n".join(lines)


def main():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    targets = [os.path.join(root, "src"), os.path.join(root, "hardware")]
    metrics = scan_python_files(targets)
    report = build_report(metrics)
    out_path = os.path.join(root, "MEMORIA_TECNICA_EXPORT.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(out_path)


if __name__ == "__main__":
    main()
