# whirr

Local experiment orchestration. Queue jobs, track metrics, wake up to results.

## Installation

```bash
uv pip install -e .
```

## Quick Start

```bash
# Initialize a project
whirr init

# Submit a job
whirr submit --name baseline -- python train.py --lr 0.01

# Start a worker
whirr worker

# Check status
whirr status

# View logs
whirr logs 1

# List runs
whirr runs
```

## Python Library

```python
import whirr

run = whirr.init(name="my-experiment", config={"lr": 0.01})

for epoch in range(100):
    loss = train_epoch()
    run.log({"loss": loss}, step=epoch)

run.summary({"best_loss": 0.1})
run.finish()
```
