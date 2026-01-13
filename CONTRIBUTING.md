# Contributing to whirr

Thank you for your interest in contributing to whirr!

## Development Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/syntropy-systems-oss/whirr.git
   cd whirr
   ```

2. Install in development mode:
   ```bash
   pip install -e ".[dev]"
   ```

3. Run tests to verify setup:
   ```bash
   pytest
   ```

## Running Tests

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/test_db.py

# Run specific test
pytest tests/test_db.py::TestJobOperations::test_create_job
```

## Code Style

- Follow PEP 8
- Use type hints where practical
- Keep functions focused and small
- Write docstrings for public APIs

## Submitting Changes

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make your changes
4. Run tests: `pytest`
5. Commit with a descriptive message
6. Push to your fork
7. Open a Pull Request

## Reporting Issues

When reporting bugs, please include:
- Python version
- Operating system
- Steps to reproduce
- Expected vs actual behavior
- Relevant logs or error messages

## Areas for Contribution

- Bug fixes
- Documentation improvements
- Test coverage
- New CLI commands
- Performance improvements

## Questions?

Open an issue for discussion before starting major changes.
