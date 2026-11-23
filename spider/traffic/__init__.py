"""Traffic: what each device talks to, built from DNS lookups.

Pi-hole (when connected) is the source. SPIder keeps its own history so it can
learn what's normal for each device and spot first contacts. Ads and trackers
that Pi-hole blocked are ignored: that's Pi-hole's job, not SPIder's.
"""
