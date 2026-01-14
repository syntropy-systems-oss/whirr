"""whirr ablate subcommand group."""

import typer

from whirr.cli.ablate.init_cmd import init
from whirr.cli.ablate.add import add
from whirr.cli.ablate.run import run
from whirr.cli.ablate.rank import rank

ablate_app = typer.Typer(
    name="ablate",
    help="Ablation study tools for identifying causal factors.",
    no_args_is_help=True,
)

# Register subcommands
ablate_app.command(name="init")(init)
ablate_app.command()(add)
ablate_app.command(
    context_settings={"allow_extra_args": True, "allow_interspersed_args": False}
)(run)
ablate_app.command()(rank)
