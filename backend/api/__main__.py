"""Lancement local de l'API HTTP."""

import uvicorn


def main() -> None:
    uvicorn.run("backend.api.app:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
