"""Push-event (webhook) policy tests.

0.1.2 changes two things about push events, and both are policy decisions that
must not drift silently:

* whether a failed webhook registration is worth retrying at all (defect
  E-010), and
* whether the webhook subsystem is wired up in the first place (the
  ``enable_webhook`` option, default off).

The second is a *load-time* decision taken in ``async_setup_entry``, so it can
only be exercised end to end by the tier 2 suite. What tier 1 can and does pin
down is the decision data itself: the constant name, its default, and the
classification function that decides retry versus repair issue.

Traceability: E-010, F-001 (push-event opt-in), E-011.
"""

from __future__ import annotations

import json

import pytest

# --------------------------------------------------------------------------
# E-010 - permanent versus transient webhook registration failures
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_deterministic_rejections_are_permanent(helper, status):
    """A deterministic rejection must stop the retry loop.

    Netatmo answers ``400`` with code ``WH006`` ("invalid webhook url") when
    the callback URL is not a publicly reachable HTTPS endpoint on port 443.
    That is a property of the deployment, not a passing condition: 0.1.1
    retried it every fifteen minutes for ever, which is useless traffic
    against a rate-limited account and a warning in the log for ever.
    """
    assert helper.webhook_failure_is_permanent(status) is True


@pytest.mark.parametrize(
    "status",
    [408, 409, 425, 429, 500, 502, 503, 504],
    ids=[
        "request-timeout",
        "conflict",
        "too-early",
        "too-many-requests",
        "server-error",
        "bad-gateway",
        "unavailable",
        "gateway-timeout",
    ],
)
def test_transient_statuses_keep_retrying(helper, status):
    """Anything that might succeed later must keep its retry.

    This is the direction upstream got wrong: a single failure abandoned the
    webhook until someone pressed Reload. The 0.1.2 classification must not
    reintroduce that by being too eager to call a failure permanent.
    """
    assert helper.webhook_failure_is_permanent(status) is False


def test_missing_status_keeps_retrying(helper):
    """A failure carrying no status is transient by default.

    ``TimeoutError`` and ``aiohttp.ClientError`` have no ``status`` attribute,
    so the caller passes ``None``. Those are exactly the failures that do
    resolve on their own, and treating an unknown failure as permanent would
    strand push events on a transient network fault.
    """
    assert helper.webhook_failure_is_permanent(None) is False


def test_throttling_overrides_a_permanent_looking_status(helper):
    """Rate limiting wears a permanent-looking status but is transient.

    Netatmo answers ``403`` when throttling, which pyatmo surfaces as
    ``ApiThrottlingError``. Classifying on the status alone would give up on a
    registration that would have succeeded a few minutes later - the very
    defect 0.1.1 was released to fix. The caller therefore passes the
    throttled flag, and it wins.
    """
    assert helper.webhook_failure_is_permanent(403, throttled=True) is False
    assert helper.webhook_failure_is_permanent(429, throttled=True) is False
    assert helper.webhook_failure_is_permanent(None, throttled=True) is False


def test_permanent_status_set_is_explicit(helper):
    """The permanent set is a closed, reviewable list.

    Stated as a test so that widening it - which weakens recovery - has to be
    a deliberate edit to an assertion, not a silent change to a frozenset.
    """
    assert helper.PERMANENT_WEBHOOK_FAILURE_STATUSES == frozenset({400, 401, 403, 404})


# --------------------------------------------------------------------------
# F-001 - push events are opt-in
# --------------------------------------------------------------------------


def test_push_events_default_to_disabled(const):
    """The default must be off, and must stay off.

    Netatmo will only register a webhook against a publicly reachable HTTPS
    endpoint. The operator is the only party who knows whether the deployment
    has one, so the integration does not assume it does. Flipping this default
    would restore the 0.1.1 behaviour for every installation that upgrades
    without touching its options.
    """
    assert const.DEFAULT_ENABLE_WEBHOOK is False


def test_option_key_is_stable(const):
    """The option key is persisted in the config entry, so it is an interface.

    Renaming it would silently reset every operator's choice back to the
    default on upgrade - and the default disables push.
    """
    assert const.CONF_ENABLE_WEBHOOK == "enable_webhook"


def test_repair_issue_key_is_stable(const):
    """The repair issue key is referenced from three places.

    ``webhook.py`` raises it, ``__init__.py`` deletes it when push is turned
    off, and ``translations/en.json`` supplies its text. A rename that missed
    one of those would leave either an undeletable issue or an untranslated
    one.
    """
    assert const.ISSUE_WEBHOOK_REJECTED == "webhook_registration_rejected"


# --------------------------------------------------------------------------
# E-011 - shipped translations must be literal text
# --------------------------------------------------------------------------


def _load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_shipped_translations_contain_no_unresolved_references(component_dir):
    """``translations/en.json`` is served verbatim, so it must be literal.

    Home Assistant expands ``[%key:...%]`` cross-references when it *builds*
    core, not when it loads translations: ``helpers/translation.py`` does a
    plain ``load_json`` followed by ``recursive_flatten``, with no
    substitution step anywhere. A core integration therefore ships a generated
    ``translations/en.json`` with the references already expanded, while a
    custom integration ships whatever is in the file.

    This fork inherited core's *source* strings, references and all, so 24
    user-facing strings - including every wind direction and the public
    weather options title - would have been displayed to the operator as the
    literal text ``[%key:component::netatmo::...%]`` (defect E-011).
    """
    en = component_dir / "translations" / "en.json"
    assert "[%key:" not in en.read_text(encoding="utf-8")


def test_strings_and_translations_agree(component_dir):
    """The authoring file and the shipped file must not drift apart.

    ``strings.json`` is what a reviewer reads and what hassfest validates;
    ``translations/en.json`` is what the operator actually sees. With no build
    step between them, the only way to keep the reviewed text and the
    displayed text identical is to require that they are the same document.
    """
    assert _load_json(component_dir / "strings.json") == _load_json(
        component_dir / "translations" / "en.json"
    )


@pytest.mark.parametrize(
    ("section", "key"),
    [
        ("issues", "partial_oauth_scopes"),
        ("issues", "webhook_registration_rejected"),
        ("issues", "watchdog_reload_exhausted"),
        ("exceptions", "command_rejected"),
    ],
)
def test_user_facing_keys_have_text(component_dir, section, key):
    """Every key the code raises must have text to show.

    A missing translation key does not fail the call that raises it; Home
    Assistant renders the raw key instead. In a repair issue or an exception
    shown to an operator, that is the difference between an instruction and a
    string of punctuation.
    """
    en = _load_json(component_dir / "translations" / "en.json")
    assert key in en[section], f"{section}.{key} has no translation"


def test_webhook_issue_text_carries_the_error_placeholder(component_dir):
    """The repair issue must show *which* rejection occurred.

    The whole point of E-010's repair issue is telling the operator what to
    change. "Netatmo refused the webhook" without the API's own message
    (``invalid webhook url (WH006)``) does not do that, and the placeholder is
    supplied by ``webhook.py`` as ``translation_placeholders={"error": ...}``.
    """
    en = _load_json(component_dir / "translations" / "en.json")
    issue = en["issues"]["webhook_registration_rejected"]
    assert "{error}" in issue["description"]
    assert issue["title"]


def test_push_events_option_step_is_translated(component_dir):
    """The new options step needs its own text, including the menu entry.

    An options *menu* was introduced in 0.1.2; without the ``init`` menu
    labels the operator is shown two bare step ids.
    """
    en = _load_json(component_dir / "translations" / "en.json")
    steps = en["options"]["step"]

    assert set(steps["init"]["menu_options"]) == {
        "push_events",
        "public_weather_areas",
    }

    push = steps["push_events"]
    assert push["title"]
    assert "enable_webhook" in push["data"]
    # The description has to state the precondition, because getting this
    # wrong is exactly what E-010 was about.
    assert "HTTPS" in push["description"]
