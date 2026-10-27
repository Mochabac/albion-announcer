# Albion Announcer

A Discord bot for an Albion Online guild. An officer presses a button, fills in a
short form, and the bot posts the announcement with the `@everyone` ping, sign-up
buttons and the follow-up messages a caller needs.

```
#officers                              #announcements
┌─────────────────────────────┐        @everyone 🧹 Limpieza! — 20:30 UTC · 📍 Martlock portal
│ 📯 Activity announcer       │        ┌───────────────────────────────────────────────┐
│ Press a button to call an   │        │ 🧹 Limpieza!                                  │
│ activity…                   │        │ 🕒 When   20:30 UTC · 16:30 your time · in 2 h│
│                             │  ───►  │ 📍 Meeting point   🛡️ Gear      🎙️ Voice      │
│ [🧹 Limpieza!]              │        │ Martlock portal    T8 · 1300 IP  #Estáticas   │
│ [⚔️ Pelea / Dive (5)]       │        │ 👥 Positions · 2/5                            │
│ [⏩ Skip de grupales!]      │        │ 🛡️ Tank — @Mocha                              │
└─────────────────────────────┘        │ 💚 Healer — open                              │
                                       │ 🔥 Dps 1 (Flami) — @Recruit                   │
                                       │ ⚔️ Dps 2 (SC) — open                          │
                                       │ 🏹 Support (Badon) — open                     │
                                       └───────────────────────────────────────────────┘
                                       [🛡️ Tank] [💚 Healer] [🔥 Dps 1 (Flami)] [⚔️ Dps 2 (SC)] [🏹 Support (Badon)]
                                       [Leave] [⚙️ Manage]
```

---

## Setup

You need [uv](https://docs.astral.sh/uv/) and a Discord application.

1. **Create the bot.** At [discord.com/developers/applications](https://discord.com/developers/applications):
   *New Application* → *Bot* → *Reset Token* → copy the token. No privileged intents are
   needed; leave them all off.
2. **Give it the token.**
   ```bash
   ./run.sh --setup           # asks for it (hidden), checks it with Discord, writes .env
   ```
   It also prints the invite link. By hand instead: `cp .env.example .env` and paste the
   token after `DISCORD_TOKEN=`. Either way `.env` stays on your machine: git ignores it.
3. **Start it.**
   ```bash
   ./run.sh
   ```
   The log prints an **invite link**. Open it, pick your server, and keep the permissions
   it asks for — *Mention @everyone, @here, and All Roles* is the one that makes the ping
   actually notify people.
4. **In Discord**, as someone with *Manage Server*:
   - `/config` shows the settings and a health check of the bot's permissions.
   - `/panel` posts the buttons in the channel you run it in. Pin it. **Whoever can see
     the panel can use it**, so put it in a channel only your callers can see (an
     officers' channel is the usual place); `/panel` warns you if the whole server can see
     it. A private channel has to let the bot in first:
     *Edit Channel → Permissions → Add members or roles*.
   - `/config channel:#announcements` picks where the announcements go. Without it they
     are posted in the channel the button was pressed in.

`./run.sh --check` validates `.env` and `activities.toml` without connecting.

---

## Using it

**Announcing.** Press an activity on the panel. Every box in the form is optional:

| Box | What to type |
|---|---|
| **When** | Empty means *now*. Otherwise a time (see below). |
| **Meeting point** | City, portal, hideout, zone. |
| **Gear / requirements** | Tier, IP, build. |
| **Notes** | Regear, loot split, anything else. |

If you are in a voice channel when you announce, the announcement links it.
A **Custom** button, if `activities.toml` has a `[custom]` table, asks for a title and a
message instead.

**Times** are read as UTC (Albion's server time) unless `/config timezone:` says otherwise.
Each reader also sees the time converted to their own clock, with a countdown.

| You type | It means |
|---|---|
| `20:30` · `2030` · `8pm` · `8:30 pm` | that time today (tomorrow if it has already passed) |
| `19 utc` · `at 20` · `a las 20` | on the hour |
| `in 45m` · `45 min` · `in 2h` · `1h30m` · `en 1 hora` | from now |
| `tomorrow 20:00` · `mañana 20:00` · `tonight 00:30` · `saturday 19 utc` · `sábado 20:00` | another day |
| anything else — `20h30`, `after reset`… | shown exactly as typed, with no countdown |

That last row is deliberate: `20h30` is a clock time to some people and a duration to
others, so the bot shows your words rather than guess the wrong hour.

**Signing up.** Members press a position under the announcement and their name appears
next to it. A position takes one player: once taken, its button is greyed out, and anyone
who still presses it on a screen that had not caught up is told privately who holds it.
Pressing another free position moves you there; *Leave* gives yours up.

**Caller tools.** *Manage*, under the announcement, opens a private menu for the caller
(and for anyone who can use the panel it was called from, and the admins):

| Button | What it posts | Who it pings |
|---|---|---|
| 🔔 **Remind** | the announcement's essentials again | the same ping as the announcement |
| 🚀 **Starting now** | a last call with the meeting point and voice channel | the people signed up |
| ✏️ **Edit** | reopens the form; a changed time or place is announced | the people signed up |
| ✅ **Finish** | a thank-you; removes the buttons | nobody |
| 🚫 **Cancel** | that it is off; removes the buttons | the people signed up |

The bot also reminds the people signed up **15 minutes before the start** on its own.
`@everyone` is only ever pinged by a person pressing a button, never automatically.

---

## Settings

`/config` with no options shows the current settings. Options (combine freely):

| Option | Default | |
|---|---|---|
| `channel` | the channel where the button is pressed | where announcements are posted |
| `ping` | `@everyone` | or `@here`, or no ping |
| `ping_role` | — | ping a role instead |
| `announcer_role` | — | add a role that may announce; pass it again to remove it |
| `language` | English | or Español. Post a new `/panel` afterwards |
| `reminder` | 15 | minutes before the start; 0 turns it off |
| `timezone` | UTC | the zone typed times are read in |
| `reset` | | back to the defaults |

**Who can announce.** Admins (*Manage Server*) always can. Otherwise, until you pick
announcer roles, the panel's channel decides: anyone who can see a panel can use it — no
role or permission of their own is needed, not even the one to ping `@everyone` (the bot
pings, not them). To let someone call activities, let them into that channel: *Edit
Channel → Permissions → Add members or roles*. They can then also run the caller tools of
anything announced from that panel; anyone else, only of what they announced themselves.

Once you pick announcer roles, only those roles (and admins) can announce and run other
people's announcements, from any panel they can see.

---

## Your own activities

`activities.toml` holds the panel: the activities, their emoji, colours, texts, the
thumbnail (any Albion item id) and which sign-up roles each one offers. It currently has
the guild's comps: *Limpieza!* (static dungeons), *Pelea / Dive* (PvP diving on statics,
as a comp of 5 and one of 7), *Skip de grupales!* (group dungeon skipping), *Ava Raid*
(8 to 10 players) and *ZvZ* (19 players, one position per weapon), each with its own spots
as sign-up buttons.
A position's `limit` is how many players it takes: 1 for every spot except the Ava Raid's
falces, which share one position of 5, and the ZvZ's Galatinas, one position of 3 (an
activity can have at most 20 roles: four rows of buttons). A role without a `limit` is
one anyone can join, and an activity with such a role shows a head count per role
instead of the list of positions. The file explains itself. After editing it:

```bash
./run.sh --check     # catches typos before Discord does
```

then restart the bot and post a fresh `/panel`. An activity with `roles = []` gets no
sign-up buttons.

---

## Keeping it running

The bot only announces while it is running. To have it start when the computer boots
(before anyone logs in, with linger on) and come back 10 seconds after any crash, run it
as a systemd user service — this is how it is installed on the guild's PC:

```ini
# ~/.config/systemd/user/albion-announcer.service
[Unit]
Description=Albion Announcer

[Service]
WorkingDirectory=%h/MochaCorp Projects/Albion Announcer
# A user service can't order itself after network-online.target (a system unit), so wait
# for NetworkManager here; the "-" lets the bot try anyway after 60 s without a network.
ExecStartPre=-/usr/bin/nm-online -q -t 60
ExecStart="%h/MochaCorp Projects/Albion Announcer/run.sh"
# uv lives in ~/.local/bin, which systemd does not search on its own.
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin
Restart=always
RestartSec=10
# Exit code 2 is a setup problem (no token, a typo in activities.toml): retrying won't help.
RestartPreventExitStatus=2

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now albion-announcer    # start now and at every boot
loginctl enable-linger "$USER"                    # at boot, not only once you log in
systemctl --user status albion-announcer
journalctl --user -u albion-announcer -f          # the log
systemctl --user restart albion-announcer         # after editing activities.toml or .env
systemctl --user disable --now albion-announcer   # back to starting it by hand
```

Only one copy runs at a time: `./run.sh` locks the project folder for as long as the bot
runs, so starting it by hand while the service is up just says so and exits.

Announcements, sign-ups and settings live in `data/announcer.db`. Buttons on old panels
and announcements keep working across restarts, and nothing pressed while Discord was
having a bad moment is lost: a roster or a closed announcement that could not be redrawn
is brought up to date as soon as the bot can, including after a restart.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| The announcement shows `@everyone` but nobody is notified | The bot lacks *Mention @everyone, @here, and All Roles* in that channel. `/config` reports it, and the bot warns the caller before posting. |
| "You can't announce activities here" | Announcer roles are picked and you hold none of them. See *Who can announce*. |
| "Only the caller or an officer can manage this activity" | It was called from a panel you can't see, or announcer roles are picked and you hold none. Announcements made before 15 Oct 2026 don't record their panel: only their caller and the admins can run those. |
| "I can't see #channel…" | The channel is private and the bot was not added to it: *Edit Channel → Permissions → Add members or roles*, and pick the bot. |
| "I can't post in #channel. I'm missing: …" | Give the bot's role those permissions in that channel. |
| `/panel` and `/config` don't appear | The invite link was opened without the `applications.commands` scope; use the link from the log. |
| Buttons still show old names after editing `activities.toml` | Post a new `/panel`; old panels keep their old labels. |
| "That form is no longer valid" | The bot was restarted while the form was open, or it sat open for over half an hour. Press the button again. |
| The roster of an announcement made hours ago updates in steps | Deliberate: Discord allows far fewer edits on messages older than an hour, so those are redrawn at most every 15 seconds. Sign-ups are recorded at once either way. |

---

## Development

```bash
uv run pytest          # 500+ tests, no network and no token needed
uv run ruff check . && uv run ruff format --check . && uv run mypy announcer tests
```

`tests/test_wire.py` is the closest thing to a live run: it feeds real gateway payloads
through discord.py and checks the exact JSON the bot would send to Discord. The tests run
against their own catalog, `tests/activities.toml`, so editing the guild's
`activities.toml` never breaks them.

| | |
|---|---|
| `announcer/service.py` | what the bot does: announce, sign up, follow up, remind |
| `announcer/views.py` | the buttons and the form |
| `announcer/render.py` | every message and embed, inside Discord's size limits |
| `announcer/timeparse.py` | reading the "When" box |
| `announcer/store.py` | SQLite: settings, events, sign-ups |
| `announcer/catalog.py` | loading and validating `activities.toml` |
| `announcer/i18n.py` | every sentence, in English and Spanish |
| `announcer/commands.py` | `/panel` and `/config` |
