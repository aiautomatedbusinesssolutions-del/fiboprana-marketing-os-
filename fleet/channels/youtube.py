"""YouTube channel adapter — stub. Comment-finding on our own videos lands later;
the rest of the pipeline is already channel-agnostic."""

from fleet.channels.base import ChannelAdapter


class YouTubeAdapter(ChannelAdapter):
    name = "youtube"

    def scan(self):
        raise NotImplementedError("YouTube channel not built yet.")
