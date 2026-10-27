"""Every sentence the bot says, in English and Spanish.

The activity names and their flavour lines are not here: those belong to the
guild and live in ``activities.toml``.
"""

from __future__ import annotations

from datetime import datetime

LANGUAGES: dict[str, str] = {"en": "English", "es": "Español"}
DEFAULT_LANGUAGE = "en"

_EN: dict[str, str] = {
    # --- panel -------------------------------------------------------------
    "panel.title": "📯 Activity announcer",
    "panel.body": (
        "Press a button to call an activity. A short form opens — time, meeting point, "
        "gear — and I post the announcement with the ping.\n\n"
        "Every field is optional: leave the time empty and it goes out as **now**. "
        "Times are read as **{tz}**."
    ),
    "panel.posted": "Panel posted. Pin it so it stays easy to find.",
    "panel.public": (
        "⚠️ Everyone in the server can see this channel, so anyone can announce with these "
        "buttons. To keep them for your callers, post the panel in a channel only they can "
        "see, or pick announcer roles with `/config announcer_role`."
    ),
    "panel.continue": "Announce without a ping",
    "warn.no_ping": (
        "⚠️ I can't ping {ping} in {channel}: I don't have the **{perm}** permission there, "
        "so the announcement would notify nobody.\n"
        "Fix the permission and press the activity button again, or go ahead without a ping."
    ),
    "warn.no_ping_role": (
        "⚠️ The role I was told to ping no longer exists, so the announcement would notify "
        "nobody.\nAn admin can pick another one with `/config`, or go ahead without a ping."
    ),
    # --- the form ------------------------------------------------------------
    "modal.when": "When",
    "modal.when.hint": "Empty = now. 20:30 · 8pm · in 45m · tomorrow 19:00 ({tz})",
    "modal.when.hint.custom": "Optional. 20:30 · 8pm · in 45m · tomorrow 19:00 ({tz})",
    "modal.when.placeholder": "20:30",
    "modal.where": "Meeting point",
    "modal.where.hint": "City, portal, hideout or zone where the group gathers.",
    "modal.where.placeholder": "Martlock portal",
    "modal.gear": "Gear / requirements",
    "modal.gear.hint": "Tier, item power, build — whatever people must bring.",
    "modal.gear.placeholder": "T8 · 1300 IP · ZvZ build",
    "modal.notes": "Notes",
    "modal.notes.hint": "Anything else: regear, loot split, how long it runs…",
    "modal.notes.placeholder": "Regear up to T8. Loot split between everyone who stays.",
    "modal.title": "Title",
    "modal.title.hint": "The headline of the announcement.",
    "modal.title.placeholder": "Guild meeting",
    "modal.body": "Message",
    "modal.body.hint": "What everyone needs to know.",
    "modal.body.placeholder": "We'll go over the plan for the next season…",
    "modal.edit": "Edit · {title}",
    # --- the announcement ------------------------------------------------------
    "field.when": "🕒 When",
    "field.where": "📍 Meeting point",
    "field.gear": "🛡️ Gear",
    "field.voice": "🎙️ Voice",
    "field.notes": "📝 Notes",
    "when.now": "**Now!**",
    "when.now.plain": "now!",
    "when.local": "your time",
    "roster.more": "+{n} more",
    "roster.positions": "👥 Positions · {filled}/{total}",
    "roster.open": "*open*",
    "footer.caller": "Called by {name}",
    "footer.signed": "Signed up: {n}",
    "status.started": "🚀 **In progress**",
    "status.finished": "✅ **Finished**",
    "status.cancelled": "🚫 **Cancelled**",
    "button.leave": "Leave",
    "button.manage": "Manage",
    # --- what the caller is told after announcing ------------------------------
    "announce.done": "✅ Announced in {channel} → {url}",
    "announce.note.no_ping": "⚠️ Posted without a ping — I couldn't mention anyone there.",
    "announce.note.time_text": (
        "ℹ️ I couldn't read the time as a clock time, so it is shown exactly as you typed it "
        "(no countdown, no reminder). Forms I understand: `20:30`, `8pm`, `19 utc`, `in 45m`, "
        "`tomorrow 20:00`."
    ),
    "announce.note.reminder": "⏰ I'll remind the people signed up at <t:{ts}:t>.",
    # --- caller tools ------------------------------------------------------------
    "manage.header": "⚙️ {head} — caller tools",
    "manage.help": (
        "**Remind** repeats the ping. **Starting now** and **Cancel** notify only the "
        "people signed up. **Finish** closes the sign-ups."
    ),
    "manage.remind": "Remind",
    "manage.start": "Starting now",
    "manage.edit": "Edit",
    "manage.finish": "Finish",
    "manage.cancel": "Cancel",
    "manage.done.remind": "🔔 Reminder sent.",
    "manage.done.start": "🚀 Marked as started and the group was called.",
    "manage.done.finish": "✅ Activity closed.",
    "manage.done.cancel": "🚫 Activity cancelled and the group was told.",
    "manage.done.edit": "✏️ Announcement updated.",
    # --- follow-up messages ------------------------------------------------------
    "followup.remind": "🔔 Reminder: {head}",
    "followup.remind.cta": "Sign up on the announcement above ☝️",
    "followup.auto": "⏰ {head} starts {rel}!",
    "followup.start": "🚀 {head} is starting now — last call!",
    "followup.finish": "✅ {head} is over. Thanks to everyone who showed up! o7",
    "followup.cancel": "🚫 {head} has been cancelled.",
    "followup.edit": "✏️ {head} was updated — here is the new plan:",
    "rel.now": "now",
    "rel.in_min": "in {n} min",
    "rel.in_hours": "in {h} h {m:02d} min",
    "rel.ago_min": "{n} min ago",
    "rel.ago_hours": "{h} h {m:02d} min ago",
    # --- errors ------------------------------------------------------------------
    "error.generic": "Something went wrong on my side. Try again in a moment.",
    "error.guild_only": "This only works inside a server.",
    "error.activity_gone": (
        "That activity no longer exists. Ask an admin to post a fresh panel with `/panel`."
    ),
    "error.channel_gone": (
        "The announcement channel I was set up with no longer exists. "
        "An admin needs to pick one again with `/config`."
    ),
    "error.not_announcer": (
        "You can't announce activities here. You need one of these roles: {roles}."
    ),
    "error.bot_perms": "I can't post in {channel}. I'm missing: {perms}.",
    "error.bot_hidden": (
        "I can't see {channel}, so I can't post there. Open **Edit Channel → Permissions**, "
        "add me, and make sure **{perm}** is allowed."
    ),
    "error.cooldown": "You announced something a moment ago. Try again in {seconds}s.",
    "error.send_failed": "Discord refused my message in {channel} ({reason}).",
    "error.event_gone": "This activity no longer exists.",
    "error.event_closed": "This activity is already closed.",
    "error.spot_taken": "{spot} is already taken by {who}. Pick another position.",
    "error.spot_full": "{spot} is already full. Pick another position.",
    "error.already_started": "This activity was already marked as started.",
    "error.not_manager": "Only the caller or an officer can manage this activity.",
    "error.remind_cooldown": "A reminder just went out. Try again in {seconds}s.",
    "error.announcement_deleted": "The announcement was deleted, so this activity is closed.",
    "error.form_expired": (
        "That form is no longer valid: I was restarted, or it stayed open too long. "
        "Press the button again."
    ),
    # --- /config -------------------------------------------------------------------
    "config.title": "⚙️ Announcer settings",
    "config.channel": "Announcement channel",
    "config.channel.same": "The channel where the button is pressed",
    "config.channel.gone": "⚠️ A channel that no longer exists",
    "config.ping": "Ping",
    "config.ping.none": "No ping",
    "config.announcers": "Who can announce",
    "config.announcers.default": (
        "Admins, and anyone who can see a panel: keep it in a channel only your callers can see"
    ),
    "config.announcers.roles": "Admins and {roles}",
    "config.language": "Language",
    "config.timezone": "Time zone for typed times",
    "config.reminder": "Automatic reminder",
    "config.reminder.off": "Off",
    "config.reminder.value": "{n} min before the start, to the people signed up",
    "config.checks": "Health check",
    "config.check.ok": "✅ I have every permission I need in {channel}.",
    "config.check.missing": "❌ In {channel} I'm missing: {perms}.",
    "config.check.hidden": (
        "❌ I can't see {channel}. Open **Edit Channel → Permissions**, add me, and make sure "
        "**{perm}** is allowed."
    ),
    "config.check.no_ping": "⚠️ I can't ping there: I'm missing **{perm}**.",
    "config.check.no_ping_role": "⚠️ The ping role no longer exists.",
    "config.footer": "Post the buttons with /panel. After changing the language, post a new panel.",
    "config.saved": "Saved.",
    "config.reset": "Settings restored to their defaults.",
    "config.bad_timezone": (
        "I don't know the time zone `{name}`. Use a name like `UTC`, `America/Bogota` "
        "or `Europe/Madrid`."
    ),
}

_ES: dict[str, str] = {
    # --- panel -------------------------------------------------------------
    "panel.title": "📯 Anuncio de actividades",
    "panel.body": (
        "Pulsa un botón para convocar una actividad. Se abre un formulario corto — hora, "
        "punto de reunión, set — y publico el anuncio con el ping.\n\n"
        "Todos los campos son opcionales: si dejas la hora vacía, sale como **ahora**. "
        "Las horas se leen en **{tz}**."
    ),
    "panel.posted": "Panel publicado. Fíjalo para tenerlo siempre a mano.",
    "panel.public": (
        "⚠️ Todo el servidor ve este canal, así que cualquiera puede anunciar con estos "
        "botones. Para dejárselos a tus callers, publica el panel en un canal que solo vean "
        "ellos o elige roles anunciadores con `/config announcer_role`."
    ),
    "panel.continue": "Anunciar sin ping",
    "warn.no_ping": (
        "⚠️ No puedo mencionar a {ping} en {channel}: ahí no tengo el permiso **{perm}**, "
        "así que el anuncio no le avisaría a nadie.\n"
        "Arregla el permiso y vuelve a pulsar el botón de la actividad, o sigue sin ping."
    ),
    "warn.no_ping_role": (
        "⚠️ El rol que debía mencionar ya no existe, así que el anuncio no le avisaría a "
        "nadie.\nUn admin puede elegir otro con `/config`, o sigue sin ping."
    ),
    # --- the form ------------------------------------------------------------
    "modal.when": "Cuándo",
    "modal.when.hint": "Vacío = ahora. 20:30 · 8pm · en 45m · mañana 19:00 ({tz})",
    "modal.when.hint.custom": "Opcional. 20:30 · 8pm · en 45m · mañana 19:00 ({tz})",
    "modal.when.placeholder": "20:30",
    "modal.where": "Punto de reunión",
    "modal.where.hint": "Ciudad, portal, hideout o zona donde se junta el grupo.",
    "modal.where.placeholder": "Portal de Martlock",
    "modal.gear": "Set / requisitos",
    "modal.gear.hint": "Tier, IP, build: lo que haya que llevar.",
    "modal.gear.placeholder": "T8 · 1300 IP · build de ZvZ",
    "modal.notes": "Notas",
    "modal.notes.hint": "Lo demás: regear, reparto del loot, cuánto dura…",
    "modal.notes.placeholder": "Regear hasta T8. El loot se reparte entre los que se queden.",
    "modal.title": "Título",
    "modal.title.hint": "El titular del anuncio.",
    "modal.title.placeholder": "Reunión de gremio",
    "modal.body": "Mensaje",
    "modal.body.hint": "Lo que todos tienen que saber.",
    "modal.body.placeholder": "Vamos a repasar el plan para la próxima temporada…",
    "modal.edit": "Editar · {title}",
    # --- the announcement ------------------------------------------------------
    "field.when": "🕒 Cuándo",
    "field.where": "📍 Punto de reunión",
    "field.gear": "🛡️ Set",
    "field.voice": "🎙️ Voz",
    "field.notes": "📝 Notas",
    "when.now": "**¡Ahora!**",
    "when.now.plain": "¡ahora!",
    "when.local": "tu hora",
    "roster.more": "+{n} más",
    "roster.positions": "👥 Posiciones · {filled}/{total}",
    "roster.open": "*libre*",
    "footer.caller": "Convoca {name}",
    "footer.signed": "Anotados: {n}",
    "status.started": "🚀 **En curso**",
    "status.finished": "✅ **Finalizada**",
    "status.cancelled": "🚫 **Cancelada**",
    "button.leave": "Salir",
    "button.manage": "Gestionar",
    # --- what the caller is told after announcing ------------------------------
    "announce.done": "✅ Anunciado en {channel} → {url}",
    "announce.note.no_ping": "⚠️ Publicado sin ping: ahí no pude mencionar a nadie.",
    "announce.note.time_text": (
        "ℹ️ No pude leer la hora como una hora de reloj, así que aparece tal cual la "
        "escribiste (sin cuenta atrás ni recordatorio). Formas que entiendo: `20:30`, `8pm`, "
        "`19 utc`, `en 45m`, `mañana 20:00`."
    ),
    "announce.note.reminder": "⏰ Les recordaré a los anotados a las <t:{ts}:t>.",
    # --- caller tools ------------------------------------------------------------
    "manage.header": "⚙️ {head} — herramientas del caller",
    "manage.help": (
        "**Recordar** repite el ping. **Salimos ya** y **Cancelar** avisan solo a los "
        "anotados. **Finalizar** cierra las inscripciones."
    ),
    "manage.remind": "Recordar",
    "manage.start": "Salimos ya",
    "manage.edit": "Editar",
    "manage.finish": "Finalizar",
    "manage.cancel": "Cancelar",
    "manage.done.remind": "🔔 Recordatorio enviado.",
    "manage.done.start": "🚀 Marcada como iniciada y se llamó al grupo.",
    "manage.done.finish": "✅ Actividad cerrada.",
    "manage.done.cancel": "🚫 Actividad cancelada y se avisó al grupo.",
    "manage.done.edit": "✏️ Anuncio actualizado.",
    # --- follow-up messages ------------------------------------------------------
    "followup.remind": "🔔 Recordatorio: {head}",
    "followup.remind.cta": "Anótate en el anuncio de arriba ☝️",
    "followup.auto": "⏰ ¡{head} empieza {rel}!",
    "followup.start": "🚀 {head} sale ya: ¡última llamada!",
    "followup.finish": "✅ {head} terminó. ¡Gracias a todos los que vinieron! o7",
    "followup.cancel": "🚫 {head} se canceló.",
    "followup.edit": "✏️ {head} cambió. Este es el nuevo plan:",
    "rel.now": "ahora",
    "rel.in_min": "en {n} min",
    "rel.in_hours": "en {h} h {m:02d} min",
    "rel.ago_min": "hace {n} min",
    "rel.ago_hours": "hace {h} h {m:02d} min",
    # --- errors ------------------------------------------------------------------
    "error.generic": "Algo falló de mi lado. Inténtalo de nuevo en un momento.",
    "error.guild_only": "Esto solo funciona dentro de un servidor.",
    "error.activity_gone": (
        "Esa actividad ya no existe. Pídele a un admin que publique un panel nuevo con `/panel`."
    ),
    "error.channel_gone": (
        "El canal de anuncios que tenía configurado ya no existe. "
        "Un admin tiene que elegir otro con `/config`."
    ),
    "error.not_announcer": (
        "No puedes anunciar actividades aquí. Necesitas uno de estos roles: {roles}."
    ),
    "error.bot_perms": "No puedo publicar en {channel}. Me falta: {perms}.",
    "error.bot_hidden": (
        "No puedo ver {channel}, así que no puedo publicar ahí. Abre **Editar canal → Permisos**, "
        "agrégame y comprueba que **{perm}** esté permitido."
    ),
    "error.cooldown": "Acabas de anunciar algo. Inténtalo de nuevo en {seconds}s.",
    "error.send_failed": "Discord rechazó mi mensaje en {channel} ({reason}).",
    "error.event_gone": "Esta actividad ya no existe.",
    "error.event_closed": "Esta actividad ya está cerrada.",
    "error.spot_taken": "{spot} ya lo tiene {who}. Elige otra posición.",
    "error.spot_full": "{spot} ya está completo. Elige otra posición.",
    "error.already_started": "Esta actividad ya estaba marcada como iniciada.",
    "error.not_manager": "Solo quien la convocó o un oficial puede gestionar esta actividad.",
    "error.remind_cooldown": "Acaba de salir un recordatorio. Inténtalo de nuevo en {seconds}s.",
    "error.announcement_deleted": "El anuncio fue borrado, así que esta actividad queda cerrada.",
    "error.form_expired": (
        "Ese formulario ya no vale: me reiniciaron o estuvo abierto demasiado tiempo. "
        "Pulsa el botón otra vez."
    ),
    # --- /config -------------------------------------------------------------------
    "config.title": "⚙️ Ajustes del anunciador",
    "config.channel": "Canal de anuncios",
    "config.channel.same": "El canal donde se pulsa el botón",
    "config.channel.gone": "⚠️ Un canal que ya no existe",
    "config.ping": "Ping",
    "config.ping.none": "Sin ping",
    "config.announcers": "Quién puede anunciar",
    "config.announcers.default": (
        "Los admins y cualquiera que pueda ver un panel: tenlo en un canal que solo vean tus "
        "callers"
    ),
    "config.announcers.roles": "Los admins y {roles}",
    "config.language": "Idioma",
    "config.timezone": "Zona horaria de las horas escritas",
    "config.reminder": "Recordatorio automático",
    "config.reminder.off": "Desactivado",
    "config.reminder.value": "{n} min antes del inicio, a los anotados",
    "config.checks": "Chequeo",
    "config.check.ok": "✅ Tengo todos los permisos que necesito en {channel}.",
    "config.check.missing": "❌ En {channel} me falta: {perms}.",
    "config.check.hidden": (
        "❌ No puedo ver {channel}. Abre **Editar canal → Permisos**, agrégame y comprueba que "
        "**{perm}** esté permitido."
    ),
    "config.check.no_ping": "⚠️ Ahí no puedo hacer ping: me falta **{perm}**.",
    "config.check.no_ping_role": "⚠️ El rol del ping ya no existe.",
    "config.footer": "Publica los botones con /panel. Si cambias el idioma, publica un panel nuevo.",
    "config.saved": "Guardado.",
    "config.reset": "Ajustes restaurados a sus valores por defecto.",
    "config.bad_timezone": (
        "No conozco la zona horaria `{name}`. Usa un nombre como `UTC`, `America/Bogota` "
        "o `Europe/Madrid`."
    ),
}

STRINGS: dict[str, dict[str, str]] = {"en": _EN, "es": _ES}

# Discord's own names for the permissions, so an admin can find them in the role editor.
_PERMISSIONS: dict[str, dict[str, str]] = {
    "en": {
        "view_channel": "View Channel",
        "send_messages": "Send Messages",
        "send_messages_in_threads": "Send Messages in Threads",
        "embed_links": "Embed Links",
        "read_message_history": "Read Message History",
        "mention_everyone": "Mention @everyone, @here, and All Roles",
    },
    "es": {
        "view_channel": "Ver canal",
        "send_messages": "Enviar mensajes",
        "send_messages_in_threads": "Enviar mensajes en hilos",
        "embed_links": "Insertar enlaces",
        "read_message_history": "Leer el historial de mensajes",
        "mention_everyone": "Mencionar @everyone, @here y todos los roles",
    },
}

_WEEKDAYS = {
    "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    "es": ("lun", "mar", "mié", "jue", "vie", "sáb", "dom"),
}
_MONTHS = {
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
    "es": ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"),
}


def _lang(lang: str) -> str:
    return lang if lang in STRINGS else DEFAULT_LANGUAGE


def t(lang: str, key: str, /, **values: object) -> str:
    """The string ``key`` in ``lang``, with its ``{placeholders}`` filled in."""
    return STRINGS[_lang(lang)][key].format(**values)


def permission_name(lang: str, flag: str) -> str:
    return _PERMISSIONS[_lang(lang)].get(flag, flag)


def day_label(lang: str, moment: datetime) -> str:
    """A short calendar day such as ``Sat 5 Oct`` / ``sáb 5 oct``."""
    lang = _lang(lang)
    return f"{_WEEKDAYS[lang][moment.weekday()]} {moment.day} {_MONTHS[lang][moment.month - 1]}"
