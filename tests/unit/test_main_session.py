from types import SimpleNamespace

from homestay_bot import main


def test_session_configuration_uses_bootstrap_without_external_credentials(monkeypatch) -> None:
    """外部 API 缺失时仍必须使用稳定的启动会话密钥和 HTTPS 域名。"""
    bootstrap = SimpleNamespace(
        session_secret="stable-bootstrap-session-secret-value",
        public_base_url="https://akros.icu",
    )
    monkeypatch.setattr(main, "BootstrapSettings", lambda: bootstrap, raising=False)
    monkeypatch.delenv("SESSION_COOKIE_HTTPS_ONLY", raising=False)

    secret, https_only = main._session_configuration()

    assert secret == bootstrap.session_secret
    assert https_only is True
