from __future__ import annotations

import asyncio
from typing import Any

import discord
import pytest

from announcer import views
from announcer.catalog import MAX_ROLES, Catalog
from announcer.i18n import LANGUAGES
from announcer.service import Draft
from announcer.store import Signup, Store

from .conftest import capped
from .fakes import new_event
from .test_i18n import LONGEST_ZONE


def buttons(view: discord.ui.View | None) -> list[dict[str, Any]]:
    assert view is not None
    return [button for row in view.to_components() for button in row["components"]]


def ids(view: discord.ui.View | None) -> list[str]:
    return [button["custom_id"] for button in buttons(view)]


async def no_submit(interaction: Any, draft: Draft) -> None: ...


# --- the panel ---------------------------------------------------------------------


@pytest.mark.parametrize("lang", LANGUAGES)
def test_panel_has_one_button_per_activity(catalog: Catalog, lang: str) -> None:
    view = views.panel_view(catalog, lang)
    rows = view.to_components()
    assert [len(row["components"]) for row in rows] == [5, 5, 4]
    assert ids(view) == [f"ao:new:{activity.key}" for activity in catalog.buttons]
    for button, activity in zip(buttons(view), catalog.buttons, strict=True):
        assert button["label"] == activity.label(lang)
        assert button["emoji"]["name"] == activity.emoji
        assert button["style"] == views.STYLES[activity.style].value
        assert len(button["label"]) <= 80 and len(button["custom_id"]) <= 100
    assert view.timeout is None


def test_a_full_catalog_still_fits_one_message(catalog: Catalog) -> None:
    # 24 activities and the custom button: Discord's ceiling of five rows of five.
    activity = catalog.activities[0]
    full = Catalog(
        roles=catalog.roles,
        activities=tuple(
            type(activity)(**{**activity.__dict__, "key": f"a{n}"}) for n in range(24)
        ),
        custom=catalog.custom,
    )
    rows = views.panel_view(full, "en").to_components()
    assert [len(row["components"]) for row in rows] == [5, 5, 5, 5, 5]


# --- the announcement ------------------------------------------------------------------


def test_announcement_buttons(store: Store, catalog: Catalog) -> None:
    event = new_event(store, roles=("tank", "healer", "dps", "support"), language="es")
    view = views.event_view(event, catalog)
    assert ids(view) == [
        f"ao:join:{event.id}:tank",
        f"ao:join:{event.id}:healer",
        f"ao:join:{event.id}:dps",
        f"ao:join:{event.id}:support",
        f"ao:leave:{event.id}",
        f"ao:manage:{event.id}",
    ]
    assert [b["label"] for b in buttons(view)] == [
        "Tanque",
        "Healer",
        "DPS",
        "Soporte",
        "Salir",
        "Gestionar",
    ]
    assert buttons(view)[0]["emoji"]["name"] == "🛡️"
    assert view.timeout is None


def test_a_full_position_is_greyed_out(store: Store, catalog: Catalog) -> None:
    event = new_event(store, roles=("tank", "healer"))
    taken = [Signup(10, "tank")]
    tank, healer, *_ = buttons(views.event_view(event, capped(catalog), taken))
    assert (tank["style"], tank["disabled"]) == (discord.ButtonStyle.secondary.value, True)
    assert (healer["style"], healer["disabled"]) == (discord.ButtonStyle.primary.value, False)
    # A role anyone can join never fills up.
    assert not any(b["disabled"] for b in buttons(views.event_view(event, catalog, taken)))


def test_an_announcement_without_signups_only_has_manage(store: Store, catalog: Catalog) -> None:
    event = new_event(store, roles=())
    assert ids(views.event_view(event, catalog)) == [f"ao:manage:{event.id}"]


def test_a_role_removed_from_the_config_keeps_its_button(store: Store, catalog: Catalog) -> None:
    event = new_event(store, roles=("retired",))
    first = buttons(views.event_view(event, catalog))[0]
    assert first["label"] == "retired" and "emoji" not in first


def test_leave_and_manage_get_a_row_of_their_own(store: Store, catalog: Catalog) -> None:
    def rows(roles: int) -> list[list[str]]:
        event = new_event(store, roles=tuple(f"r{n}" for n in range(roles)))
        return [
            [button["custom_id"].split(":")[1] for button in row["components"]]
            for row in views.event_view(event, catalog).to_components()
        ]

    assert rows(4) == [["join"] * 4, ["leave", "manage"]]
    assert rows(5) == [["join"] * 5, ["leave", "manage"]]
    assert rows(17) == [["join"] * 5] * 3 + [["join"] * 2, ["leave", "manage"]]
    # The most roles an activity may have fill Discord's five rows; one more would need a sixth.
    assert rows(MAX_ROLES) == [["join"] * 5] * 4 + [["leave", "manage"]]
    with pytest.raises(ValueError, match="row"):
        rows(MAX_ROLES + 1)
    assert rows(0) == [["manage"]]


@pytest.mark.parametrize(
    ("status", "actions"),
    [
        ("open", ["remind", "start", "edit", "finish", "cancel"]),
        ("started", ["remind", "edit", "finish", "cancel"]),
    ],
)
def test_caller_tools_follow_the_state(store: Store, status: str, actions: list[str]) -> None:
    event = new_event(store, status=status)
    view = views.manage_view(event)
    assert ids(view) == [f"ao:act:{event.id}:{action}" for action in actions]
    assert view is not None and view.timeout is None


@pytest.mark.parametrize("status", ["finished", "cancelled"])
def test_a_closed_activity_has_no_tools(store: Store, status: str) -> None:
    assert views.manage_view(new_event(store, status=status)) is None


# --- custom ids ---------------------------------------------------------------------------


EXAMPLES = {
    views.PanelButton: ["ao:new:zvz", "ao:new:zvz!", "ao:new:custom", "ao:new:a_1"],
    views.JoinButton: ["ao:join:1:tank", "ao:join:123456789012345678:r_2"],
    views.LeaveButton: ["ao:leave:1", "ao:leave:123456789012345678"],
    views.ManageButton: ["ao:manage:42"],
    views.ActionButton: [f"ao:act:7:{action}" for action in views.ACTIONS],
}


def test_every_custom_id_belongs_to_exactly_one_button_class() -> None:
    assert set(EXAMPLES) == set(views.DYNAMIC_ITEMS)
    for owner, examples in EXAMPLES.items():
        for custom_id in examples:
            matching = [
                cls
                for cls in views.DYNAMIC_ITEMS
                if cls.__discord_ui_compiled_template__.fullmatch(custom_id)
            ]
            assert matching == [owner], custom_id


@pytest.mark.parametrize(
    "custom_id",
    [
        "ao:new:",
        "ao:new:ZvZ",
        "ao:new:zvz!!",
        "ao:join:1",
        "ao:join:x:tank",
        "ao:leave:",
        "ao:act:1:delete",
        "ao:manage:1:2",
    ],
)
def test_malformed_custom_ids_match_nothing(custom_id: str) -> None:
    for cls in views.DYNAMIC_ITEMS:
        assert not cls.__discord_ui_compiled_template__.fullmatch(custom_id)


async def test_buttons_are_rebuilt_from_their_custom_id() -> None:
    async def rebuild(cls: Any, custom_id: str) -> Any:
        match = cls.__discord_ui_compiled_template__.fullmatch(custom_id)
        button: Any = discord.ui.Button(label="kept", custom_id=custom_id)
        item = await cls.from_custom_id(None, button, match)
        assert item.item is button and item.custom_id == custom_id
        return item

    panel = await rebuild(views.PanelButton, "ao:new:zvz")
    assert (panel.key, panel.force) == ("zvz", False)
    forced = await rebuild(views.PanelButton, "ao:new:fame!")
    assert (forced.key, forced.force) == ("fame", True)
    join = await rebuild(views.JoinButton, "ao:join:12:healer")
    assert (join.event_id, join.role) == (12, "healer")
    assert (await rebuild(views.LeaveButton, "ao:leave:12")).event_id == 12
    assert (await rebuild(views.ManageButton, "ao:manage:12")).event_id == 12
    action = await rebuild(views.ActionButton, "ao:act:12:cancel")
    assert (action.event_id, action.action) == (12, "cancel")


async def test_buttons_survive_the_timeout_of_a_private_message(store: Store) -> None:
    """discord.py gives the view of an ephemeral message a 15-minute timeout. When that
    view expires, the button classes must stay registered: if they did not, opening the
    caller tools once would kill every caller-tools button fifteen minutes later."""
    client = discord.Client(intents=discord.Intents.none())
    client.add_dynamic_items(*views.DYNAMIC_ITEMS)
    registry = client._connection._view_store
    patterns = {cls.__discord_ui_compiled_template__ for cls in views.DYNAMIC_ITEMS}
    assert patterns <= set(registry._dynamic_items)

    view = views.manage_view(new_event(store))
    assert view is not None
    view.timeout = 0.01  # what InteractionResponse.send_message(ephemeral=True) does, sped up
    registry.add_view(view, 555)
    await asyncio.sleep(0.05)

    assert view.is_finished()
    assert patterns <= set(registry._dynamic_items)

    # Explicitly stopping a view must not unregister them either.
    other = views.event_view(new_event(store), catalog=Catalog(roles={}, activities=()))
    registry.add_view(other, 556)
    other.stop()
    assert patterns <= set(registry._dynamic_items)


# --- the form ---------------------------------------------------------------------------------


def labels(form: views.AnnounceForm) -> list[dict[str, Any]]:
    return form.to_dict()["components"]


@pytest.mark.parametrize("lang", LANGUAGES)
@pytest.mark.parametrize("custom", [False, True])
async def test_form_fits_discords_limits(lang: str, custom: bool) -> None:
    form = views.AnnounceForm(
        heading="🏛️ " + "x" * 60, custom=custom, lang=lang, timezone=LONGEST_ZONE, submit=no_submit
    )
    payload = form.to_dict()
    assert len(payload["title"]) <= 45
    assert len(payload["components"]) == 4
    for label in payload["components"]:
        assert label["type"] == 18  # a Label wrapping one text input
        assert 0 < len(label["label"]) <= 45
        assert 0 < len(label["description"]) <= 100
        box = label["component"]
        assert box["type"] == 4 and len(box["placeholder"]) <= 100
        assert 1 <= box["max_length"] <= 4000
        assert "value" not in box
    required = [label["component"]["required"] for label in payload["components"]]
    assert required == ([True, True, False, False] if custom else [False] * 4)


async def test_activity_form_asks_when_where_gear_notes() -> None:
    form = views.AnnounceForm(
        heading="⚔️ ZvZ", custom=False, lang="en", timezone="UTC", submit=no_submit
    )
    assert form.title == "⚔️ ZvZ"
    assert [label["label"] for label in labels(form)] == [
        "When",
        "Meeting point",
        "Gear / requirements",
        "Notes",
    ]
    assert labels(form)[0]["description"].endswith("(UTC)")
    assert [label["component"]["style"] for label in labels(form)] == [1, 1, 1, 2]


async def test_custom_form_asks_title_message_when_where() -> None:
    form = views.AnnounceForm(
        heading="📢 Anuncio libre", custom=True, lang="es", timezone="UTC", submit=no_submit
    )
    assert [label["label"] for label in labels(form)] == [
        "Título",
        "Mensaje",
        "Cuándo",
        "Punto de reunión",
    ]
    assert labels(form)[2]["description"].startswith("Opcional.")


async def test_form_collects_a_draft() -> None:
    received: list[Draft] = []

    async def submit(interaction: Any, draft: Draft) -> None:
        received.append(draft)

    form = views.AnnounceForm(
        heading="⚔️ ZvZ", custom=False, lang="en", timezone="UTC", submit=submit
    )
    form.when._value = "  20:30 "
    form.where._value = "Martlock portal"
    assert form.gear is not None and form.notes is not None
    form.gear._value = "T8"
    form.notes._value = None  # Discord sends nothing for a box left empty
    await form.on_submit(None)  # type: ignore[arg-type]
    assert received == [Draft(when="20:30", location="Martlock portal", gear="T8")]

    custom = views.AnnounceForm(heading="📢", custom=True, lang="en", timezone="UTC", submit=submit)
    assert custom.headline is not None and custom.body is not None
    custom.headline._value = "Guild meeting"
    custom.body._value = "On voice at reset."
    assert custom.draft() == Draft(title="Guild meeting", body="On voice at reset.")


async def test_edit_form_is_prefilled(store: Store) -> None:
    event = new_event(
        store, when_text="20:30", location="Martlock portal", gear="T8", notes="Regear."
    )
    form = views.AnnounceForm(
        heading="Edit · ⚔️ ZvZ",
        custom=False,
        lang="en",
        timezone="UTC",
        submit=no_submit,
        event=event,
    )
    assert [label["component"].get("value") for label in labels(form)] == [
        "20:30",
        "Martlock portal",
        "T8",
        "Regear.",
    ]
    # Submitting it untouched gives back exactly what the event holds.
    assert form.draft() == Draft(
        when="20:30", location="Martlock portal", gear="T8", notes="Regear."
    )

    notice = new_event(
        store, activity="custom", title="Guild meeting", message="On voice.", roles=()
    )
    custom = views.AnnounceForm(
        heading="Edit", custom=True, lang="en", timezone="UTC", submit=no_submit, event=notice
    )
    assert [label["component"].get("value") for label in labels(custom)] == [
        "Guild meeting",
        "On voice.",
        None,
        None,
    ]
