"""The weather agent (2026-10-22). Read this first.

    config.py   the gate (WEATHER_ENABLED), fixed rules, the settings (AI Agents -> Overview)
    nws.py      the ONLY code that talks to the weather service: three reads of api.weather.gov
    rules.py    reading a forecast (PURE): a visit's hours, dry days, heavy rain, a storm passed
    service.py  the pass: refresh every 3 h, keep the Weather items in Dispatch, texts to staff

Source: the National Weather Service - the official US forecast, free, no key - hourly chance of
rain and rain amount for each job's 2.5 km grid, and the official alerts for Florida. Only a
rounded map position ever leaves the CRM. Every flag quotes the NWS numbers and when the NWS
made them; a newer forecast updates or clears it.

It also feeds scheduling: `dispatch.booking.slots` skips a time with rain at or above the
threshold at the house (and says each slot's chance), and the Dispatch assistant has a `weather`
tool (dry days, the visit's chance, heavy rain coming). Never texts a customer, never writes to
Zuper. Off unless WEATHER_ENABLED; texts only to the numbers in its settings, Shadow first.
"""
