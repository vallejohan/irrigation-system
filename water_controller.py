#!/usr/bin/env python3

import RPi.GPIO as GPIO
import time
import threading
import paho.mqtt.client as mqtt
import os

# GPIO pin setup
VALVE_PIN = 17
LED_PIN = 27
BUTTON_PIN = 22

GPIO.setmode(GPIO.BCM)
GPIO.setup(VALVE_PIN, GPIO.OUT)
GPIO.output(VALVE_PIN, GPIO.LOW)
GPIO.setup(LED_PIN, GPIO.OUT)
GPIO.output(LED_PIN, GPIO.LOW)
GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# Valve variables
valve_on = False
valve_timer = None

# MQTT variables
moisture_level = 0
moisture_threshold = 0
manual_mode = False
valve_open_time_m = 0
valve_open_time_s = 5
open_valve = False

mqtt_host = "homeassistant.local"
mqtt_port = 1883
mqtt_username = os.getenv("MQTT_USERNAME")
mqtt_password = os.getenv("MQTT_PASSWORD")

# Lock for thread-safe valve operations
valve_lock = threading.Lock()

mqtt_client = mqtt.Client()
mqtt_client.username_pw_set(mqtt_username, mqtt_password)

def heartbeat_loop():
    while True:
        mqtt_client.publish("garden/controller/heartbeat", payload="online", retain=False)
        print("[HEARTBEAT] Published 'online'")
        time.sleep(30)

def publish_valve_state():
    """Publish valve state over MQTT."""
    state = "on" if valve_on else "off"
    mqtt_client.publish("garden/valve_state", state)
    print(f"[MQTT] Published valve_state: {state}")


def set_valve(state: bool):
    """Turn the valve and LED on or off."""
    global valve_on, valve_timer

    with valve_lock:
        if valve_timer:
            valve_timer.cancel()
            valve_timer = None

        valve_on = state
        GPIO.output(VALVE_PIN, GPIO.HIGH if state else GPIO.LOW)
        GPIO.output(LED_PIN, GPIO.HIGH if state else GPIO.LOW)
        print(f"[VALVE] {'ON' if state else 'OFF'}")
        publish_valve_state()


def auto_close_valve_after(duration_sec):
    """Turn off the valve after a delay."""
    global valve_timer

    def timeout():
        print("[TIMER] Auto closing valve.")
        set_valve(False)

    valve_timer = threading.Timer(duration_sec, timeout)
    valve_timer.start()


def handle_mqtt_data():
    """Process current moisture and threshold values to decide if valve should turn on."""
    global valve_open_time_m, valve_open_time_s, open_valve

    if not manual_mode and moisture_level < moisture_threshold:
        print("[MQTT] Moisture too low. Opening valve.")
        duration = valve_open_time_m * 60 + valve_open_time_s
        set_valve(True)
        auto_close_valve_after(duration)
    elif manual_mode and open_valve:
        if open_valve == True:
            print("[MQTT] Manual mode active. Opening valve.")
        else:
            print("[MQTT] Manual mode active. Closing valve.")
        set_valve(open_valve)


def on_connect(client, userdata, flags, rc):
    print("[MQTT] Connected with result code " + str(rc))
    client.subscribe("garden/moisture_level")
    client.subscribe("garden/moisture_threshold")
    client.subscribe("garden/manual_mode")
    client.subscribe("garden/open_valve")
    client.subscribe("garden/valve_open_time_m")
    client.subscribe("garden/valve_open_time_s")

def on_message(client, userdata, msg):
    global moisture_level, moisture_threshold, manual_mode
    global valve_open_time_m, valve_open_time_s, open_valve

    topic = msg.topic
    payload = msg.payload.decode()

    try:
        if topic == "garden/moisture_level":
            moisture_level = int(payload)
        elif topic == "garden/moisture_threshold":
            moisture_threshold = int(payload)
        elif topic == "garden/manual_mode":
            manual_mode = payload.lower() == "true"
        elif topic == "garden/valve_open_time_m":
            valve_open_time_m = int(payload)
        elif topic == "garden/valve_open_time_s":
            valve_open_time_s = int(payload)
        elif topic == "garden/open_valve":
            if payload.lower() == "true":
                open_valve = True
            else:
                open_valve = False

        print(f"[MQTT] Received {topic}: {payload}")
        handle_mqtt_data()

    except ValueError as e:
        print(f"[MQTT ERROR] Could not parse payload for {topic}: {payload} ({e})")


# Button callback using interrupt
def button_callback(channel):
    time.sleep(0.03)
    if GPIO.input(BUTTON_PIN) == GPIO.LOW:
        print("[BUTTON] Interrupt triggered. Toggling valve.")
        set_valve(not valve_on)
    else:
        print("[BUTTON] Spurious interrupt ignored.")

# Start MQTT client
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message
mqtt_client.connect(mqtt_host, 1883, 60)

# Start MQTT client loop thread
mqtt_thread = threading.Thread(target=mqtt_client.loop_forever)
mqtt_thread.daemon = True
mqtt_thread.start()

# Start heartbeat loop thread
heartbeat_thread = threading.Thread(target=heartbeat_loop)
heartbeat_thread.daemon = True
heartbeat_thread.start()

# Register button interrupt with debounce
GPIO.add_event_detect(BUTTON_PIN, GPIO.FALLING, callback=button_callback, bouncetime=200)

print("System ready. Listening for MQTT messages and button presses...")

try:
    while True:
        time.sleep(1)

except KeyboardInterrupt:
    print("\n[EXIT] Cleaning up...")

finally:
    if valve_timer:
        valve_timer.cancel()
    GPIO.cleanup()