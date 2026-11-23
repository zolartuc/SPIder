"""Turn raw lookups into something people understand: the site behind a name, and what kind of thing it is."""

import ipaddress

# Two-label public suffixes common enough to matter at home (so bbc.co.uk stays bbc.co.uk).
_TWO_LEVEL = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "ltd.uk", "plc.uk", "me.uk", "net.uk",
    "com.au", "net.au", "org.au", "co.nz", "co.jp", "ne.jp", "co.kr", "co.za", "co.in",
    "com.br", "com.cn", "com.tw", "com.hk", "com.sg", "com.mx", "com.tr", "co.id",
}

# Names that are about the local network, not the internet.
_LOCAL_SUFFIXES = (".lan", ".local", ".home", ".internal", ".arpa", ".localdomain", ".home.arpa", ".router")


def is_noise(domain: str) -> bool:
    d = domain.lower().rstrip(".")
    return not d or "." not in d or d == "localhost" or d.endswith(_LOCAL_SUFFIXES)


def base_domain(domain: str) -> str:
    """rr3---sn-aigl6nek.googlevideo.com -> googlevideo.com; news.bbc.co.uk -> bbc.co.uk."""
    d = domain.lower().rstrip(".")
    try:
        ipaddress.ip_address(d)
        return d
    except ValueError:
        pass
    parts = d.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in _TWO_LEVEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


CATEGORIES = {
    "Streaming": [
        "netflix.com", "nflxvideo.net", "nflxso.net", "nflxext.com", "youtube.com", "googlevideo.com", "ytimg.com",
        "youtu.be", "disneyplus.com", "bamgrid.com", "dssott.com", "primevideo.com", "aiv-cdn.net", "aiv-delivery.net",
        "spotify.com", "scdn.co", "spotifycdn.com", "bbc.co.uk", "bbci.co.uk", "twitch.tv", "ttvnw.net", "jtvnw.net",
        "itv.com", "itvx.com", "channel4.com", "nowtv.com", "sky.com", "skycdp.com", "apple.tv", "plex.tv", "plex.direct",
        "deezer.com", "tidal.com", "soundcloud.com", "audible.com", "audible.co.uk", "crunchyroll.com", "paramountplus.com",
    ],
    "Gaming": [
        "xboxlive.com", "xbox.com", "playstation.net", "playstation.com", "sonyentertainmentnetwork.com", "nintendo.net",
        "nintendo.com", "steampowered.com", "steamcommunity.com", "steamcontent.com", "steamserver.net", "steamstatic.com",
        "epicgames.com", "epicgames.dev", "unrealengine.com", "riotgames.com", "leagueoflegends.com", "battle.net",
        "blizzard.com", "ea.com", "origin.com", "ubi.com", "ubisoft.com", "rockstargames.com", "minecraft.net",
        "mojang.com", "roblox.com", "rbxcdn.com", "gog.com", "nvidia.com", "geforcenow.com",
    ],
    "Social & chat": [
        "facebook.com", "fbcdn.net", "facebook.net", "instagram.com", "cdninstagram.com", "whatsapp.net", "whatsapp.com",
        "tiktokv.com", "tiktokcdn.com", "tiktok.com", "byteoversea.com", "snapchat.com", "sc-cdn.net", "x.com",
        "twitter.com", "twimg.com", "reddit.com", "redd.it", "redditmedia.com", "discord.com", "discord.gg",
        "discordapp.com", "discordapp.net", "telegram.org", "signal.org", "messenger.com", "linkedin.com", "licdn.com",
        "pinterest.com", "pinimg.com", "threads.net", "bsky.app",
    ],
    "Smart home": [
        "tuyaeu.com", "tuyaus.com", "tuyacn.com", "tuyain.com", "sonos.com", "sonos.net", "ring.com", "hivehome.com",
        "alertme.com", "meethue.com", "smartthings.com", "nest.com", "amazonalexa.com", "alexa.com", "ecobee.com",
        "tplinkcloud.com", "tapo.com", "wyze.com", "arlo.com", "eufylife.com", "shelly.cloud", "meross.com",
        "home-assistant.io", "nabu.casa", "ewelink.cc", "coolkit.cc", "miwifi.com", "mi.com", "xiaomi.com",
        "xiaomi.net", "io.mi.com", "samsungiotcloud.com", "lifx.com", "switchbot.net", "netatmo.net",
    ],
    "Makers & printing": [
        "bambulab.com", "bambulab.cn", "printables.com", "prusa3d.com", "thingiverse.com", "octoprint.org",
        "makerworld.com", "creality.com", "crealitycloud.com", "github.com", "githubusercontent.com", "raspberrypi.com",
        "raspberrypi.org", "pypi.org", "pythonhosted.org", "docker.com", "docker.io",
    ],
    "Work & productivity": [
        "office.com", "office365.com", "office.net", "microsoftonline.com", "sharepoint.com", "outlook.com",
        "teams.microsoft.com", "skype.com", "zoom.us", "zoom.com", "slack.com", "slack-edge.com", "atlassian.com",
        "atlassian.net", "notion.so", "google.com", "gmail.com", "googleusercontent.com", "webex.com", "gotomeeting.com",
        "salesforce.com", "okta.com", "duosecurity.com", "1password.com", "bitwarden.com", "lastpass.com",
    ],
    "Cloud & storage": [
        "icloud.com", "icloud-content.com", "dropbox.com", "dropboxapi.com", "onedrive.com", "1drv.com", "live.com",
        "box.com", "amazonaws.com", "cloudfront.net", "azure.com", "azureedge.net", "windows.net", "googleapis.com",
        "gstatic.com", "cloudflare.com", "akamaized.net", "akamaihd.net", "fastly.net", "backblazeb2.com",
    ],
    "Updates & system": [
        "windowsupdate.com", "update.microsoft.com", "microsoft.com", "msftconnecttest.com", "msftncsi.com",
        "apple.com", "mzstatic.com", "aaplimg.com", "cdn-apple.com", "android.com", "gvt1.com", "gvt2.com",
        "ubuntu.com", "debian.org", "canonical.com", "ntp.org", "pool.ntp.org", "samsungcloudsolution.com",
        "samsungotn.net", "samsungcloud.com", "samsungdm.com", "lgtvsdp.com", "lge.com", "roku.com", "mozilla.org",
        "mozilla.net", "firefox.com", "digicert.com", "letsencrypt.org", "sectigo.com", "ocsp.apple.com",
    ],
    "Shopping": [
        "amazon.com", "amazon.co.uk", "ebay.com", "ebay.co.uk", "etsy.com", "argos.co.uk", "tesco.com",
        "aliexpress.com", "temu.com", "shein.com", "paypal.com", "klarna.com",
    ],
}

_LOOKUP = {d: cat for cat, domains in CATEGORIES.items() for d in domains}
OTHER = "Other"


def category(base: str) -> str:
    if base in _LOOKUP:
        return _LOOKUP[base]
    # e.g. "something.microsoft.com" folded to base already; try one more parent for odd cases.
    parent = ".".join(base.split(".")[1:])
    return _LOOKUP.get(parent, OTHER)
