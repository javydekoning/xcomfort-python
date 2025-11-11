import argparse
import asyncio
import logging
from xcomfort import Bridge

def observe_device(device):
    device.state.subscribe(lambda state: print(f"Device state [{device.device_id}] '{device.name}': {state}"))

async def main(ip: str, auth_key: str):
    bridge = Bridge(ip, auth_key)

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

if __name__ == "__main__":
    # Configure debug logging
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    parser = argparse.ArgumentParser(description="Test xComfort Bridge connection")
    parser.add_argument("--ip", required=True, help="IP address of the xComfort Bridge")
    parser.add_argument("--auth-key", required=True, help="Authentication key for the xComfort Bridge")
    
    args = parser.parse_args()
    
    asyncio.run(main(args.ip, args.auth_key))