from endstone.plugin import Plugin
from endstone.event import event_handler, BroadcastMessageEvent, PlayerDeathEvent, PlayerChatEvent, PlayerJoinEvent, PlayerQuitEvent
from endstone.lang import Translatable
from pathlib import Path
import threading
from discord_webhook import DiscordWebhook
from PIL import Image, ImageDraw, ImageFont
import re
import time
from typing import cast
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap 

class ChatRelay(Plugin):
    def install(self):
        folder = Path(self.data_folder)
        folder.mkdir(parents=True, exist_ok=True)
        cfg_path = folder / "config.yml"
        self.yml = YAML()
        self.yml.version = (1, 2)
        self.yml.preserve_quotes = True
        defaults = [
            ("webhook_url", "", "Discord webhook URL"),
            ("font_path", "", "Path to custom font file"),
            ("player_message_type", "'image'", 'ONLY applies to player messages. Options: "image" | "plaintext". Use any other option to not send these messages at all.'),
            ("join_or_leave_message_type", "'image'", 'ONLY applies to join/leave messages. Options: "image" | "plaintext". Use any other option to not send these messages at all.'),
            ("other_messages_type", "'image'", 'ONLY applies to messages not listed beforehand (death messages, broadcasted messages...). Options: "image" | "plaintext". Use any other option to not send these messages at all.'),
            ("show_warning_on_bad_config_value", "false", "Weather to log warnings if a key is wrong. Certain keys (like the three before this one) let you use an invalid option for some special functionality.")
        ]
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                existing = self.yml.load(f)
            if not isinstance(existing, CommentedMap):
                existing = CommentedMap(existing or {})
        else:
            existing = CommentedMap()

        for key, default, comment in defaults:
            if key not in existing:
                existing[key] = default
                existing.yaml_add_eol_comment(comment, key)

        with open(cfg_path, "w", encoding="utf-8") as f:
            self.yml.dump(existing, f)

        self.yaml_config = dict(existing)

    def on_enable(self):
        self.install()

        self.webhook_url = cast(str, self.yaml_config.get("webhook_url"))
        self.font_path = cast(str, self.yaml_config.get("font_path"))
        if not self.webhook_url or not self.font_path:
            self.logger.error("Chatrelay will NOT function! Fill out both `webhook_url` and `font_path` before reloading the plugin.")

        self.register_events(self)

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

    def remove_mentions(self, message: str) -> str:
        text = re.sub(r'@everyone', 'Everyone', message)
        text = re.sub(r'@here', 'Here', text)
        text = re.sub(r'@(\w+)', r'\1', text)
        return text

    def _send_as_image(self, message: str):
        if len(message) > 100:
            DiscordWebhook(
                url=self.webhook_url,
                content=self.remove_mentions(message=message),
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

    def send_player_message(self, message: str):
        if message == "":
            return
        def task():
            message_type = cast(str, self.yaml_config.get("'player_message_type'", "image"))
            try:
                if message_type == "image":
                    self._send_as_image(message=message)
                elif message_type == "plaintext": 
                    DiscordWebhook(
                        url=self.webhook_url,
                        content=self.remove_mentions(message=message),
                    ).execute()
                else:
                    if not self.yaml_config.get("other_messages_type"):
                        self.logger.warning(f'Message "{message}" was not sent because your config has an invalid option.')
            except Exception as e:
                print("ERROR !!!!!!!!!!!!! 😭😭😭 Check following!! 🥺🥺🥺 ", e)
        if not self.last_message == message:
            threading.Thread(target=task, daemon=True).start()
            self.last_message = message

    def send_join_or_leave_message(self, message: str):
        if message == "":
            return
        def task():
            message_type = cast(str, self.yaml_config.get("'join_or_leave_message_type'", "image"))
            try:
                if message_type == "image":
                    self._send_as_image(message=message)
                elif message_type == "plaintext": 
                    DiscordWebhook(
                        url=self.webhook_url,
                        content=self.remove_mentions(message=message),
                    ).execute()
                else:
                    if not self.yaml_config.get("other_messages_type"):
                        self.logger.warning(f'Message "{message}" was not sent because your config has an invalid option.')
            except Exception as e:
                print("ERROR !!!!!!!!!!!!! 😭😭😭 Check following!! 🥺🥺🥺 ", e)
        if not self.last_message == message:
            threading.Thread(target=task, daemon=True).start()
            self.last_message = message

    def send_other_message(self, message: str):
        if message == "":
            return
        def task():
            message_type = cast(str, self.yaml_config.get("'other_messages_type'", "image"))
            try:
                if message_type == "image":
                    self._send_as_image(message=message)
                elif message_type == "plaintext": 
                    DiscordWebhook(
                        url=self.webhook_url,
                        content=self.remove_mentions(message=message),
                    ).execute()
                else:
                    if not self.yaml_config.get("other_messages_type"):
                        self.logger.warning(f'Message "{message}" was not sent because your config has an invalid option.')
            except Exception as e:
                print("ERROR !!!!!!!!!!!!! 😭😭😭 Check following!! 🥺🥺🥺 ", e)
        if not self.last_message == message:
            threading.Thread(target=task, daemon=True).start()
            self.last_message = message

    def resolve_message(self, message: str | Translatable | None) -> str:
        if not message: return ""
        elif isinstance(message, Translatable):
            message = self.server.language.translate(str(message.text), locale=self.server.language.locale, params=message.params) 
        else: 
            message = str(message)
        # if it's None: return, if it's a translateable: translate into server's locale
        return message



    @event_handler
    def on_broadcast_message(self, event: BroadcastMessageEvent):
        message = self.resolve_message(event.message)
        self.send_other_message(message)

    @event_handler
    def on_player_death(self, event: PlayerDeathEvent):
        message = self.resolve_message(event.death_message)
        self.send_other_message(message)

    @event_handler
    def on_player_chat(self, event: PlayerChatEvent):
        message = f"<{event.player.name}> {event.message}"
        self.send_player_message(message)


    @event_handler
    def on_player_join(self, event: PlayerJoinEvent):
        message = self.resolve_message(event.join_message)
        self.send_join_or_leave_message(message)
    
    @event_handler
    def on_player_quit(self, event: PlayerQuitEvent):
        message = self.resolve_message(event.quit_message)
        self.send_join_or_leave_message(message)