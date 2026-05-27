import sys
import asyncio
from bleak import BleakClient, BleakScanner

# Set Windows loop policy
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

CHARACTERISTIC_SENSOR_VALUES_UUID = "f3f9c7ce-46ee-4205-89ac-abe64e626c0f"
CHARACTERISTIC_STEP_ANALYSIS_UUID = "4eb776dc-cf99-4af7-b2d3-ad0f791a79dd"
DEVICE_ADDRESS = "F4:1B:9B:AF:20:5D"

def notification_handler_sensor(sender, data):
    print(f"[SENSOR VALUES] Received {len(data)} bytes from {sender}: {data.hex()}")

def notification_handler_step(sender, data):
    print(f"[STEP ANALYSIS] Received {len(data)} bytes from {sender}: {data.hex()}")

async def main():
    print(f"Connecting to {DEVICE_ADDRESS}...")
    async with BleakClient(DEVICE_ADDRESS) as client:
        if client.is_connected:
            print("Connected successfully!")
            
            # Start notifications for both characteristics to see which one works
            print("Starting step analysis notification...")
            await client.start_notify(CHARACTERISTIC_STEP_ANALYSIS_UUID, notification_handler_step)
            
            print("Starting sensor values notification...")
            await client.start_notify(CHARACTERISTIC_SENSOR_VALUES_UUID, notification_handler_sensor)
            
            print("\nListening for 20 seconds. Please walk/shake the shoe...")
            for i in range(20):
                await asyncio.sleep(1)
                print(f"Time elapsed: {i+1}s")
                
            print("Stopping notifications...")
            await client.stop_notify(CHARACTERISTIC_SENSOR_VALUES_UUID)
            await client.stop_notify(CHARACTERISTIC_STEP_ANALYSIS_UUID)
        else:
            print("Failed to connect.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"Error: {e}")
