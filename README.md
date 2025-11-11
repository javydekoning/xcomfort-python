# xcomfort-python

Unofficial python package for communicating with Eaton xComfort Bridge

## Installation

This project uses [uv](https://docs.astral.sh/uv/) for dependency management. You can install it using:

```bash
# Install uv if you haven't already
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install the package in development mode
uv pip install -e .
```

## Usage

Easy mode:

```sh
uv run main.py --ip x.x.x.x --auth-key "ABCD1234WXYZ"
```

Roll your own;

```python
import asyncio
from xcomfort import Bridge

def observe_device(device):
    device.state.subscribe(lambda state: print(f"Device state [{device.device_id}] '{device.name}': {state}"))

async def main():
    bridge = Bridge(<ip_address>, <auth_key>)

    runTask = asyncio.create_task(bridge.run())

    devices = await bridge.get_devices()

    for device in devices.values():
        observe_device(device)
        
    # Wait 50 seconds. Try flipping the light switch manually while you wait
    await asyncio.sleep(50) 

    # Turn off all the lights.
    # for device in devices.values():
    #     await device.switch(False)
    #
    # await asyncio.sleep(5)

    await bridge.close()
    await runTask

asyncio.run(main())
```

## Development

### Running Tests

You can run the tests using uvx without any local dependency management:

```bash
./run_tests.sh
```

To run Github workflows locally

Install [act](https://nektosact.com/installation/index.html) and run flows locally using `act`. 

### Dependencies

The project includes the following dependencies:

- `aiohttp` - For async HTTP client functionality
- `rx` - For reactive programming
- `pycryptodome` - For cryptographic operations
