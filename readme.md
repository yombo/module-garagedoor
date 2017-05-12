Summary
=======

This Yombo module makes 'dumb' garages into smart ones by adding logic to control
a garage door (or devices that act like garage doors).

Additional details about this module at: https://yombo.net/modules/garagedoor/

Learn about [Yombo Gateway](https://yombo.net/).

Usage
=====

This module extends the capabilities of the [Yombo Gateway](https://yombo.net/)
by adding logic to control garage door (or devices that act like garage doors).

Garage Door
================

> **Warning**
> Caution should be used when operating garage doors remotely. Video cameras or other sensors should be used to validate the safety of operation.

To act like a garage door, 2 devices are required:

1. An input sensor to determine if the garage door is 'closed' (or low, 0, off).
2. A control device connected to a relay or some output controller.

Optional: An "all clear" device can be used to note if it's safe to operate the garage door. This device can be any real or virtual device or state. If the device or state has one of the following values, this module will accept and process commands: on, 1, high, ok

This module uses the 'control' device and pulses the relay to simulate someone pushing a garage door opener button.

Installation
============

Simply mark this module as being used by the gateway, and the gateway will download and install this module automatically.

Requirements
============

See "Garage Door" above for requirements.

License
=======

The [Yombo](https://yombo.net/) team and other contributors hopes that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

See LICENSE file for full details.
