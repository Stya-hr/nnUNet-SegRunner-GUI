# Application metadata (static fields)

APP_NAME: str = "膝关节软骨分割工具"
APP_VERSION: str = "v2"
APP_BUILD: str = "2025-12-02"
APP_DESCRIPTION: str = "GUI for nnUNet segmentation pipeline"
APP_VENDOR: str = ""
APP_COPYRIGHT: str = "© 2025"


def format_about(runtime_lines: list[str]) -> str:
    """Compose About dialog text using static metadata and runtime lines.

    runtime_lines: extra lines like Python/Qt versions, mode, nnUNet availability
    """
    head = f"{APP_NAME}\n版本: {APP_VERSION}\n构建: {APP_BUILD}"
    if APP_VENDOR or APP_COPYRIGHT:
        tail = "\n\n" + " ".join([s for s in (APP_VENDOR, APP_COPYRIGHT) if s])
    else:
        tail = ""
    extra = "\n".join(runtime_lines) if runtime_lines else ""
    return head + ("\n\n" + extra if extra else "") + tail + "\n"
