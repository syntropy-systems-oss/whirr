"""Main CLI entry point for whirr."""

import typer

from whirr.cli.cancel import cancel
from whirr.cli.compare import compare
from whirr.cli.dashboard import dashboard
from whirr.cli.doctor import doctor
from whirr.cli.export import export
from whirr.cli.init_cmd import init
from whirr.cli.logs import logs
from whirr.cli.retry import retry
from whirr.cli.runs import runs, show
from whirr.cli.server_cmd import server
from whirr.cli.status import status
from whirr.cli.submit import submit
from whirr.cli.sweep import sweep
from whirr.cli.watch import watch
from whirr.cli.worker import worker

app = typer.Typer(
    name="whirr",
    help="Local experiment orchestration. Queue jobs, track metrics, wake up to results.",
    no_args_is_help=True,
    add_completion=False,
)

# Register commands
app.command()(init)
app.command(
    context_settings={"allow_extra_args": True, "allow_interspersed_args": False}
)(submit)
app.command()(status)
app.command()(worker)
app.command()(logs)
app.command()(cancel)
app.command()(retry)
app.command()(sweep)
app.command()(watch)
app.command()(runs)
app.command()(show)
app.command()(doctor)
app.command()(dashboard)
app.command()(compare)
app.command(name="export")(export)
app.command()(server)


if __name__ == "__main__":
    app()
