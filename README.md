# moon-rabbit

Custom stateful discord bot.

[Invite moon-rabbit](https://discord.com/api/oauth2/authorize?client_id=884131362251079730&permissions=515396455488&scope=bot)

You will need [uv](https://docs.astral.sh/uv/). Run `uv venv && uv pip install -r requirements.txt` and then `uv run`.

For development (mypy, black): `uv pip install -r requirements-dev.txt`.

Create own bot on [discord](https://discord.com/developers/applications) and export `DISCORD_TOKEN` env variable, e.g. by `.env` file:

```
DISCORD_TOKEN=<your token>
```

Permissions integer: 240518548544.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for details.

## License

Apache 2.0; see [`LICENSE`](LICENSE) for details.

## Disclaimer

This project is not an official Google project. It is not supported by
Google and Google specifically disclaims all warranties as to its quality,
merchantability, or fitness for a particular purpose.
