"""
main.py
-------
Entry point for the monthly budget app.

    python main.py
"""

from app import create_app


def main() -> None:
    root, _app = create_app()
    root.mainloop()


if __name__ == "__main__":
    main()
