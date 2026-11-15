from types import SimpleNamespace

import litellm

from app import llm


def antwort(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


def test_chat_reicht_die_konfiguration_an_litellm(client, monkeypatch):
    aufrufe = []
    monkeypatch.setattr(litellm, "completion", lambda **kw: aufrufe.append(kw) or antwort("Hallo"))
    nachrichten = [{"role": "user", "content": "Hi"}]

    assert llm.chat(nachrichten) == "Hallo"
    # Standard ohne Angaben: Beispielmodell, Adresse und Schlüssel nimmt LiteLLM vom Anbieter
    assert aufrufe[0] == {"model": "openai/gpt-4o-mini", "messages": nachrichten, "max_tokens": 1000,
                          "api_base": None, "api_key": None, "timeout": 60}


def test_chat_ohne_api_base_fuer_anbieter_mit_eigener_adresse(client, monkeypatch):
    aufrufe = []
    monkeypatch.setattr(litellm, "completion", lambda **kw: aufrufe.append(kw) or antwort("ok"))
    client.application.config.update(LLM_MODEL="anthropic/claude-sonnet-5-5", LLM_API_BASE="",
                                     LLM_API_KEY="geheim")

    llm.chat([{"role": "user", "content": "Hi"}], max_tokens=50)
    assert (aufrufe[0]["model"], aufrufe[0]["api_base"], aufrufe[0]["api_key"]) == \
        ("anthropic/claude-sonnet-5-5", None, "geheim")
