import argparse

from nexus.translators.to_copilot import translate_agent_to_copilot
from nexus.translators.to_markdown import translate_agent_to_markdown
from nexus.translators.to_python import translate_agent_to_python


def main():
    parser = argparse.ArgumentParser(description="Nexus ARC CLI")
    subparsers = parser.add_subparsers(dest="command")

    # Translate
    translate_parser = subparsers.add_parser("translate", help="Translate definitions")
    translate_sub = translate_parser.add_subparsers(dest="subcommand")

    # Translate to Markdown
    md_parser = translate_sub.add_parser("to-markdown", help="Convert YAML to Markdown")
    md_parser.add_argument("file", help="YAML file to translate")

    # Translate to Copilot
    copilot_parser = translate_sub.add_parser(
        "to-copilot", help="Convert YAML to Copilot Instructions"
    )
    copilot_parser.add_argument("file", help="YAML file to translate")

    # Translate to Python
    python_parser = translate_sub.add_parser("to-python", help="Convert YAML to Python class")
    python_parser.add_argument("file", help="YAML file to translate")

    bridge_parser = subparsers.add_parser("command-bridge", help="Run the Nexus command bridge")
    bridge_parser.add_argument("--host", default=None, help="Bridge host")
    bridge_parser.add_argument("--port", type=int, default=None, help="Bridge port")
    bridge_parser.add_argument(
        "--auth-token",
        default=None,
        help="Shared bearer token used by bridge clients",
    )

    args = parser.parse_args()

    if args.command == "translate":
        if args.subcommand == "to-markdown":
            print(translate_agent_to_markdown(args.file))
        elif args.subcommand == "to-copilot":
            print(translate_agent_to_copilot(args.file))
        elif args.subcommand == "to-python":
            print(translate_agent_to_python(args.file))
    elif args.command == "command-bridge":
        from nexus.command_bridge_service import run_command_bridge

        run_command_bridge(
            host=args.host,
            port=args.port,
            auth_token=args.auth_token,
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
