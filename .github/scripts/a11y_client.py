"""Long-lived AT-SPI client. Calls RegisterEvent so WebKit enables a11y, then blocks."""
import time
from jeepney import DBusAddress, new_method_call
from jeepney.io.blocking import open_dbus_connection

session = open_dbus_connection(bus="SESSION")
addr = session.send_and_get_reply(new_method_call(
    DBusAddress("/org/a11y/bus", bus_name="org.a11y.Bus", interface="org.a11y.Bus"),
    "GetAddress")).body[0]
session.close()

conn = open_dbus_connection(bus=addr)
registry = DBusAddress("/org/a11y/atspi/registry",
                       bus_name="org.a11y.atspi.Registry",
                       interface="org.a11y.atspi.Registry")
conn.send_and_get_reply(new_method_call(
    registry, "RegisterEvent", "sass",
    ("object:state-changed:focused", [], "")))
print("a11y client registered, holding bus connection", flush=True)
while True:
    time.sleep(3600)
