from endstone.plugin import Plugin
from endstone.event import event_handler, BroadcastMessageEvent, PlayerDeathEvent, PlayerChatEvent, PlayerJoinEvent, PlayerQuitEvent, EventPriority
from endstone.lang import Translatable
from pathlib import Path
import threading
from discord_webhook import DiscordWebhook, DiscordEmbed
from PIL import Image, ImageDraw, ImageFont
import re
import time
import requests
from typing import cast, Any
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap 
from pydantic import BaseModel, Field
from .etc import commented_map_to_dict

class MessageTypeConfig(BaseModel):
    player: str = "image"
    join_leave: str = "image"
    other: str = "image"

class EmbedColorConfig(BaseModel):
    player: int = 5614830
    join_leave: int = 3066993
    other: int = 15158332

class EmbedTitleConfig(BaseModel):
    player: str = "Chat"
    join_leave: str = "Server Event"
    other: str = "Server Notification"

class EmbedAvatarConfig(BaseModel):
    player: bool = True
    join_leave: bool = True
    other: bool = False

class EmbedConfig(BaseModel):
    color: EmbedColorConfig = Field(default_factory=EmbedColorConfig)
    title: EmbedTitleConfig = Field(default_factory=EmbedTitleConfig)
    footer_text: str = "Chatrelay"
    avatar: EmbedAvatarConfig = Field(default_factory=EmbedAvatarConfig)

class WebhooksConfig(BaseModel):
    player: list[str] = Field(default_factory=list)
    join_leave: list[str] = Field(default_factory=list)
    other: list[str] = Field(default_factory=list)

class ImageRendererConfig(BaseModel):
    max_width: int = 512
    max_height: int = 30
    padding: int = 5
    length_threshold: int = 100
    delay: float = 1.0

class ChatRelayConfig(BaseModel):
    webhook_url: str = ""
    webhooks: WebhooksConfig = Field(default_factory=WebhooksConfig)
    fonts: list[str] = Field(default_factory=list)
    message_type: MessageTypeConfig = Field(default_factory=MessageTypeConfig)
    show_warning_on_bad_config_value: bool = False
    embed: EmbedConfig = Field(default_factory=EmbedConfig)
    image_renderer: ImageRendererConfig = Field(default_factory=ImageRendererConfig)

class ChatRelay(Plugin):
    api_version = "0.11"
    _config: ChatRelayConfig

    @property
    def config(self) -> ChatRelayConfig:
        return self._config

    def get_webhook_urls(self, category: str) -> list[str]:
        webhooks = getattr(self.config.webhooks, category)
        if not webhooks:
            return [self.config.webhook_url] if self.config.webhook_url else []
        return webhooks

    def install(self):
        folder = Path(self.data_folder)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "fonts").mkdir(exist_ok=True)
        cfg_path = folder / "config.yml"
        self.yml = YAML()
        self.yml.version = (1, 2)
        self.yml.preserve_quotes = True
        defaults = [
            ("webhook_url", "", "Primary (and fallback) Discord webhook URL"),
            ("webhooks.player", [], "List of Discord webhook URLs for player messages"),
            ("webhooks.join_leave", [], "List of Discord webhook URLs for join/leave messages"),
            ("webhooks.other", [], "List of Discord webhook URLs for other messages"),
            ("fonts", [], "List of font filenames (searched in the 'fonts' folder) or full paths. Supports fallbacks."),
            ("image_renderer.max_width", 512, "Maximum width of the rendered image"),
            ("image_renderer.max_height", 30, "Maximum height of the rendered image"),
            ("image_renderer.padding", 5, "Padding around the text"),
            ("image_renderer.length_threshold", 100, "Message length threshold to switch to plaintext"),
            ("image_renderer.delay", 1.0, "Delay in seconds between sending images"),
            ("message_type.player", "image", 'ONLY applies to player messages. Options: image | plaintext | embed.'),
            ("message_type.join_leave", "image", 'ONLY applies to join/leave messages. Options: image | plaintext | embed.'),
            ("message_type.other", "image", 'ONLY applies to other messages (death, broadcast...). Options: image | plaintext | embed.'),
            ("show_warning_on_bad_config_value", False, "Whether to log warnings if a key is wrong."),
            ("embed.color.player", 5614830, "Embed color for player messages (decimal format)"),
            ("embed.color.join_leave", 3066993, "Embed color for join/leave messages (decimal format)"),
            ("embed.color.other", 15158332, "Embed color for other messages (decimal format)"),
            ("embed.title.player", "Chat", "Embed title for player messages; leave blank for no title"),
            ("embed.title.join_leave", "Server Event", "Embed title for join/leave messages; leave blank for no title"),
            ("embed.title.other", "Server Notification", "Embed title for other messages; leave blank for no title"),
            ("embed.footer_text", "Chatrelay", "Footer text for all embeds; leave blank for no footer"),
            ("embed.avatar.player", True, "Show player avatar in player message embeds"),
            ("embed.avatar.join_leave", True, "Show player avatar in join/leave embeds"),
            ("embed.avatar.other", False, "Show avatar in other message embeds"),
        ]
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                existing = self.yml.load(f)
            if not isinstance(existing, CommentedMap):
                existing = CommentedMap(existing or {})
            
            # Migration for font_path
            if "font_path" in existing:
                old_path = existing.pop("font_path")
                if old_path and "fonts" not in existing:
                    existing["fonts"] = [old_path]

            # Migration for old flat keys to nested keys
            migrations = {
                "player_message_type": "message_type.player",
                "join_or_leave_message_type": "message_type.join_leave",
                "other_messages_type": "message_type.other",
                "embed_color_player": "embed.color.player",
                "embed_color_join_leave": "embed.color.join_leave",
                "embed_color_other": "embed.color.other",
                "embed_title_player": "embed.title.player",
                "embed_title_join_leave": "embed.title.join_leave",
                "embed_title_other": "embed.title.other",
                "embed_footer_text": "embed.footer_text",
                "embed_avatar_player": "embed.avatar.player",
                "embed_avatar_join_leave": "embed.avatar.join_leave",
                "embed_avatar_other": "embed.avatar.other",
            }

            for old_key, new_key_path in migrations.items():
                if old_key in existing:
                    val = existing.pop(old_key)
                    keys = new_key_path.split(".")
                    curr = existing
                    for k in keys[:-1]:
                        if k not in curr: curr[k] = CommentedMap()
                        curr = curr[k]
                    if keys[-1] not in curr:
                        curr[keys[-1]] = val
        else:
            existing = CommentedMap()

        for key, default, comment in defaults:
            keys = key.split(".")
            curr = existing
            for k in keys[:-1]:
                if k not in curr: curr[k] = CommentedMap()
                curr = curr[k]
            
            if keys[-1] not in curr:
                curr[keys[-1]] = default
            
            # Always ensure the comment is there
            curr.yaml_add_eol_comment(comment, keys[-1])

        with open(cfg_path, "w", encoding="utf-8") as f:
            self.yml.dump(existing, f)

        self._config = ChatRelayConfig(**commented_map_to_dict(existing))

    def on_enable(self):
        self.install()

        self.resolved_fonts = []
        fonts_dir = Path(self.data_folder) / "fonts"
        
        using_image_mode = any(
            t == "image" for t in [self.config.message_type.player, self.config.message_type.join_leave, self.config.message_type.other]
        )

        if using_image_mode:
            for f in self.config.fonts:
                p = Path(f)
                if p.exists():
                    self.resolved_fonts.append(str(p.absolute()))
                else:
                    p_local = fonts_dir / f
                    if p_local.exists():
                        self.resolved_fonts.append(str(p_local.absolute()))
                    else:
                        self.logger.error(f"Font not found: {f} (checked absolute path and {fonts_dir})")

        has_webhooks = (
            self.config.webhook_url
            or self.config.webhooks.player
            or self.config.webhooks.join_leave
            or self.config.webhooks.other
        )

        if not has_webhooks:
            self.logger.error("Chatrelay will NOT function! Fill out `webhook_url` or at least one webhook in `webhooks` before reloading the plugin.")
        elif using_image_mode and not self.resolved_fonts:
            self.logger.error("Chatrelay will NOT function! No valid fonts found but 'image' type is enabled.")
        else:
            self.register_events(self)
        self.logger.info("If your config is bad, delete it and Chatrelay will make a new one for you.")
        
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

    def _send_as_image(self, message: str, category: str):
        webhook_urls = self.get_webhook_urls(category)
        if len(message) > self.config.image_renderer.length_threshold:
            for url in webhook_urls:
                DiscordWebhook(
                    url=url,
                    content=self.remove_mentions(message=message),
                ).execute()
            return

        chunks = self.parse_minecraft(message)

        max_width = self.config.image_renderer.max_width
        max_height = self.config.image_renderer.max_height
        padding = self.config.image_renderer.padding
        
        loaded_fonts = []
        for f_path in self.resolved_fonts:
            try:
                loaded_fonts.append(ImageFont.truetype(f_path, max_height))
            except Exception:
                continue
        
        if not loaded_fonts:
            return

        def get_font_for_char(c):
            if c.isspace():
                return loaded_fonts[0]
            
            # The last font is our source of truth (the robust one)
            robust_font = loaded_fonts[-1]
            
            # If it's not supported by the robust one, don't even try artistic fonts.
            if robust_font.getmask(c).getbbox() is None:
                return robust_font
            
            # If it IS supported by the robust one, try artistic fonts in order.
            for f in loaded_fonts[:-1]:
                if f.getmask(c).getbbox() is not None:
                    return f
            
            return robust_font

        def text_width(t: str):
            w = 0
            for c in t:
                f = get_font_for_char(c)
                w += f.getlength(c)
            return w

        lines = []
        current_line = []
        current_width = 0

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

            # Estimate y position using the first font's metrics
            sample_font = loaded_fonts[0]
            bbox = sample_font.getbbox("Ay")
            y = (max_height - (bbox[3] - bbox[1])) // 2

            x = padding
            for text, style in line:
                color = tuple(int(style["color"][i:i+2], 16) for i in (1, 3, 5)) + (255,)
                for c in text:
                    f = get_font_for_char(c)
                    draw.text((x, y), c, font=f, fill=color)
                    x += f.getlength(c)

            png_path = folder / f"mc_render_{int(time.time()*1000)}.png"
            img.save(png_path)

            with open(png_path, "rb") as f:
                data = f.read()

            for url in webhook_urls:
                DiscordWebhook(
                    url=url,
                    content=" ",
                    files={png_path.name: (png_path.name, data)},
                ).execute()

            try:
                png_path.unlink()
            except:
                pass

            time.sleep(self.config.image_renderer.delay)

    def _resolve_to_plaintext(self, message: str) -> str:
        msg = re.sub(r'[\x00-\x1f\x7f]', '', message)
        segments = re.split(r'§[a-z0-9]', msg)
        codes = ["n"] + re.findall(r'§([a-z0-9])', msg)
        state = {"l": False, "o": False}
        parsed = []
        for c, t in zip(codes, segments):
            if c == "r": state = {"l": False, "o": False}
            elif c == "l": state["l"] = True
            elif c == "o": state["o"] = True
            wrap = "***" if state["l"] and state["o"] else "**" if state["l"] else "_" if state["o"] else ""
            parsed.append((re.sub(r'([*_\\])', r'\\\1', t), wrap))
        result = ""
        for text, wrap in parsed:
            if wrap:
                stripped = text.strip()
                leading = text[:len(text) - len(text.lstrip())]
                trailing = text[len(text.rstrip()):]
                result += leading + wrap + stripped + wrap + trailing
            else:
                result += text
        return result

    def _send_as_plaintext(self, message: str, category: str):
        webhook_urls = self.get_webhook_urls(category)
        for url in webhook_urls:
            DiscordWebhook(
                url=url,
                content=self.remove_mentions(self._resolve_to_plaintext(message=message))
            ).execute()

    def _send_as_embed(self, message: str, category: str, player: str = ""):
        plain = self.remove_mentions(self._resolve_to_plaintext(message=message))
        
        # Access namespaced config values
        color = getattr(self.config.embed.color, category, 0)
        title = getattr(self.config.embed.title, category, "")
        footer = self.config.embed.footer_text
        show_avatar = getattr(self.config.embed.avatar, category, False)
        
        embed = DiscordEmbed(title=title, description=plain, color=color)
        embed.set_footer(text=footer)
        
        if player:
            try:
                response = requests.get(f"https://mcprofile.io/api/v1/bedrock/gamertag/{player}", timeout=5)
                data = response.json()
                icon_url = data.get("icon")
                if show_avatar and icon_url:
                    embed.set_thumbnail(url=icon_url)
                embed.set_author(name=player)
            except Exception:
                embed.set_author(name=player)
            
        webhook_urls = self.get_webhook_urls(category)
        for url in webhook_urls:
            webhook = DiscordWebhook(url=url)
            webhook.add_embed(embed)
            webhook.execute()

    def _warn(self, message: str):
        self.server.scheduler.run_task(self, lambda: self.logger.warning(message))

    def send_player_message(self, message: str, player: str = ""):
        if message == "":
            return
        def task():
            message_type = self.config.message_type.player
            try:
                if message_type == "image":
                    self._send_as_image(message=message, category="player")
                elif message_type == "plaintext": 
                    self._send_as_plaintext(message=message, category="player")
                elif message_type == "embed":
                    self._send_as_embed(message=message, category="player", player=player)
                else:
                    if self.config.show_warning_on_bad_config_value:
                        self._warn(f'Message "{message}" was not sent because your config has an invalid option: {message_type}')
            except Exception as e:
                print("ERROR !!!!!!!!!!!!! 😭😭😭 Check following!! 🥺🥺🥺 ", e)
        if not self.last_message == message:
            threading.Thread(target=task, daemon=True).start()
            self.last_message = message

    def send_join_or_leave_message(self, message: str, player: str = ""):
        if message == "":
            return
        def task():
            message_type = self.config.message_type.join_leave
            try:
                if message_type == "image":
                    self._send_as_image(message=message, category="join_leave")
                elif message_type == "plaintext": 
                    self._send_as_plaintext(message=message, category="join_leave")
                elif message_type == "embed":
                    self._send_as_embed(message=message, category="join_leave", player=player)
                else:
                    if self.config.show_warning_on_bad_config_value:
                        self._warn(f'Message "{message}" was not sent because your config has an invalid option: {message_type}')
            except Exception as e:
                print("ERROR !!!!!!!!!!!!! 😭😭😭 Check following!! 🥺🥺🥺 ", e)
        if not self.last_message == message:
            threading.Thread(target=task, daemon=True).start()
            self.last_message = message

    def send_other_message(self, message: str):
        if message == "":
            return
        def task():
            message_type = self.config.message_type.other
            try:
                if message_type == "image":
                    self._send_as_image(message=message, category="other")
                elif message_type == "plaintext": 
                    self._send_as_plaintext(message=message, category="other")
                elif message_type == "embed":
                    self._send_as_embed(message=message, category="other")
                else:
                    if self.config.show_warning_on_bad_config_value:
                        self._warn(f'Message "{message}" was not sent because your config has an invalid option: {message_type}')
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
        return message



    @event_handler(priority=EventPriority.MONITOR) # type: ignore
    def on_broadcast_message(self, event: BroadcastMessageEvent):
        message = self.resolve_message(event.message)
        self.send_other_message(message)

    @event_handler(priority=EventPriority.MONITOR) # type: ignore
    def on_player_death(self, event: PlayerDeathEvent):
        message = self.resolve_message(event.death_message)
        self.send_other_message(message)

    @event_handler(priority=EventPriority.MONITOR) # type: ignore
    def on_player_chat(self, event: PlayerChatEvent):
        message = f"<{event.player.name}> {event.message}"
        self.send_player_message(message, player=event.player.name)

    @event_handler(priority=EventPriority.MONITOR) # type: ignore
    def on_player_join(self, event: PlayerJoinEvent):
        message = self.resolve_message(event.join_message)
        self.send_join_or_leave_message(message, player=event.player.name)
    
    @event_handler(priority=EventPriority.MONITOR) # type: ignore
    def on_player_quit(self, event: PlayerQuitEvent):
        message = self.resolve_message(event.quit_message)
        self.send_join_or_leave_message(message, player=event.player.name)