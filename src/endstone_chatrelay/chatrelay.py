from endstone.plugin import Plugin
from endstone.event import event_handler, BroadcastMessageEvent, PlayerDeathEvent, PlayerChatEvent, PlayerJoinEvent, PlayerQuitEvent
from endstone.lang import Translatable

from pathlib import Path
import threading
from discord_webhook import DiscordWebhook
from PIL import Image, ImageDraw, ImageFont
import yaml
import re
import time
from typing import cast

class ChatRelay(Plugin):
    def install(self):
        folder = Path(self.data_folder)
        folder.mkdir(parents=True, exist_ok=True)
        cfg_path = folder / "config.yml"
        if not cfg_path.exists():
            cfg_path.write_text("webhook_url: ''\nfont_path: ''\n", encoding="utf-8")
        with open(cfg_path, "r", encoding="utf-8") as f:
            self.yaml_config = yaml.safe_load(f)

    def on_enable(self):
        self.install()
        self.webhook_url = cast(str, self.yaml_config.get("webhook_url"))
        self.font_path = cast(str, self.yaml_config.get("font_path"))
        self.register_events(self)

        self.logger.info(f"WEBHOOK URL: {self.webhook_url}")
        self.logger.info(f"FONT PATH: {self.font_path}")

        self.last_message = ""

    def parse_minecraft(self, msg: str):
        chunks = []
        style = {'color':'#FFFFFF','bold':False,'italic':False,'underline':False,'strike':False}
        buf = ""
        i = 0
        COLOR_MAP = {
            '0': '#000000',
            '1': '#0000AA',
            '2': '#00AA00',
            '3': '#00AAAA',
            '4': '#AA0000',
            '5': '#AA00AA',
            '6': '#FFAA00',
            '7': '#AAAAAA',
            '8': '#555555',
            '9': '#5555FF',
            'a': '#55FF55',
            'b': '#55FFFF',
            'c': '#FF5555',
            'd': '#FF55FF',
            'e': '#FFFF55',
            'f': '#FFFFFF',

            'g': '#DDD605',
            'h': '#E3D4D1',
            'i': '#CECACA',
            'j': '#443A3B',
            'm': '#971607',
            'n': '#B4684D',
            'p': '#DEB12D',
            'q': '#119F36',
            's': '#2CBAA8',
            't': '#21497B',
            'u': '#9A5CC6',
            'v': '#EB7114',
        }

        while i < len(msg):
            if msg[i]=="§" and i+1<len(msg):
                code = msg[i+1].lower(); i+=1
                if buf: chunks.append((buf, style.copy())); buf=""
                if code=='k': pass
                elif code=='r': style = {'color':'#FFFFFF','bold':False,'italic':False,'underline':False,'strike':False}
                elif code in COLOR_MAP: style['color']=COLOR_MAP[code]
                elif code=='l': style['bold']=True
                elif code=='o': style['italic']=True
                elif code=='n': style['underline']=True
                elif code=='m': style['strike']=True
            else: buf+=msg[i]
            i+=1
        if buf: chunks.append((buf, style.copy()))
        return chunks

    def render_and_send(self, message: str):
        def task():
            try:
                if len(message) > 100:
                    DiscordWebhook(
                        url=self.webhook_url,
                        content=message,
                    ).execute()
                    return

                chunks = self.parse_minecraft(message)

                max_width, max_height, padding = 512, 30, 5
                font = ImageFont.truetype(self.font_path, max_height)

                lines = []
                current_line = []
                current_width = 0

                def text_width(t: str):
                    bbox = font.getbbox(t)
                    return bbox[2] - bbox[0]

                for text, style in chunks:
                    parts = re.split(r"( )", text)
                    for part in parts:
                        w = text_width(part)
                        if current_width + w + padding * 2 > max_width and current_line:
                            lines.append(current_line)
                            current_line = []
                            current_width = 0
                        current_line.append((part, style))
                        current_width += w

                if current_line:
                    lines.append(current_line)

                folder = Path(self.data_folder, "htmlrendertext")
                folder.mkdir(exist_ok=True)

                for line in lines:
                    img = Image.new("RGBA", (max_width, max_height), (0, 0, 0, 0))
                    draw = ImageDraw.Draw(img)

                    sample_text = "".join(t for t, _ in line)
                    bbox = font.getbbox(sample_text)
                    y = (max_height - (bbox[3] - bbox[1])) // 2

                    x = padding
                    for text, style in line:
                        color = tuple(int(style["color"][i:i+2], 16) for i in (1, 3, 5)) + (255,)
                        draw.text((x, y), text, font=font, fill=color)
                        x += text_width(text)

                    png_path = folder / f"mc_render_{int(time.time()*1000)}.png"
                    img.save(png_path)

                    with open(png_path, "rb") as f:
                        data = f.read()

                    DiscordWebhook(
                        url=self.webhook_url,
                        content=" ",
                        files={png_path.name: (png_path.name, data)},
                    ).execute()

                    try:
                        png_path.unlink()
                    except:
                        pass

                    time.sleep(1)

            except Exception as e:
                print("ERROR !!!!!!!!!!!!! 😭😭😭 Check following!! 🥺🥺🥺 ", e)

        if not self.last_message == message:
            threading.Thread(target=task, daemon=True).start()
            self.last_message = message

    @event_handler
    def on_broadcast_message(self, event: BroadcastMessageEvent):
        if not event.message: return 
        elif isinstance(event.message, Translatable):
            message = str(event.message.text) 
            message = self.server.language.translate(message, locale=self.server.language.locale, params=event.message.params) 
        else: 
            message = str(event.message)
        # if it's None: return, if it's a translateable: translate into server's locale

        self.render_and_send(message)

    @event_handler
    def on_player_death(self, event: PlayerDeathEvent):
        if not event.death_message: return 
        elif isinstance(event.death_message, Translatable):
            message = str(event.death_message.text) 
            message = self.server.language.translate(message, locale=self.server.language.locale, params=event.death_message.params) 
        else: 
            message = str(event.death_message)
        # if it's None: return, if it's a translateable: translate into server's locale
        self.render_and_send(message)

    @event_handler
    def on_player_chat(self, event: PlayerChatEvent):
        message = f"<{event.player.name}> {event.message}"
        self.render_and_send(message)

    @event_handler
    def on_player_join(self, event: PlayerJoinEvent):
        if not event.join_message: return 
        elif isinstance(event.join_message, Translatable):
            message = str(event.join_message.text) 
            message = self.server.language.translate(message, locale=self.server.language.locale, params=event.join_message.params) 
        else:
            message = str(event.join_message)
        # if it's None: return, if it's a translateable: translate into server's locale
        self.render_and_send(message)
    
    @event_handler
    def on_player_quit(self, event: PlayerQuitEvent):
        if not event.quit_message: return 
        elif isinstance(event.quit_message, Translatable):
            message = str(event.quit_message.text) 
            message = self.server.language.translate(message, locale=self.server.language.locale, params=event.quit_message.params) 
        else:
            message = str(event.quit_message)
        # if it's None: return, if it's a translateable: translate into server's locale
        self.render_and_send(message)