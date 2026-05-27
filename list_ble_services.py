import sys
import asyncio
from bleak import BleakClient

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

addresses = ["F4:1B:9B:AF:20:5D", "D0:A3:43:7D:01:BD"]

async def explore_device(address):
    print(f"\n==========================================")
    print(f"Connecting to {address}...")
    try:
        async with BleakClient(address) as client:
            if client.is_connected:
                print(f"Connected to {address}!")
                print("Discovering services and characteristics:")
                for service in client.services:
                    print(f"\n[Service] {service.uuid} ({service.description})")
                    for char in service.characteristics:
                        print(f"  - [Characteristic] {char.uuid} ({char.description}) | Properties: {char.properties}")
            else:
                print(f"Failed to connect to {address}")
    except Exception as e:
        print(f"Error exploring {address}: {e}")

async def main():
    for address in addresses:
        await explore_device(address)

if __name__ == "__main__":
    asyncio.run(main())
