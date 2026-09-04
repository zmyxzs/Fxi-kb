"""
fxi.cli.main - kb 命令行入口总路由
"""

import typer
from fxi.cli.commands_ops import app as ops_app
from fxi.cli.commands_project import app as project_app
from fxi.cli.commands_search import app as search_app

app = typer.Typer(
    name="kb",
    help="Fxi (kb) - 小说世界观知识库与因果动态状态管理命令行工具",
    add_completion=False
)

# 注册子命令族
app.add_typer(ops_app, name="ops")
app.add_typer(project_app, name="project")
app.add_typer(search_app, name="query")

# 顶级快捷别名命令
app.command("rebuild")(ops_app.registered_commands[0].callback)
app.command("search")(search_app.registered_commands[0].callback)
app.command("state")(search_app.registered_commands[1].callback)
app.command("ripple")(search_app.registered_commands[2].callback)
for _cmd in project_app.registered_commands:
    if _cmd.name == "import":
        app.command("import")(_cmd.callback)


if __name__ == "__main__":
    app()
