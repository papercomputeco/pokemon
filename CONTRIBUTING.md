# Contributing

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) for dependency management
- A legally obtained Pokemon Red ROM file (place in `rom/`)

## Running Tests

```bash
uv run pytest --cov
```

100% test coverage is enforced via `fail_under = 100` in `pyproject.toml`. CI will reject PRs that drop below this threshold.

## How to Contribute

The Speed Run Milestones table in the README defines target turn counts for each game checkpoint. Fork the repo, improve the strategy or navigation, and post your numbers.

Areas where contributions are welcome:

- **Navigation**: Better pathfinding, new waypoint routes, handling more map transitions
- **Battle strategy**: Smarter move selection, switching, item usage
- **Evolution harness**: New fitness metrics, mutation strategies
- **New milestones**: Extending the game progress further
