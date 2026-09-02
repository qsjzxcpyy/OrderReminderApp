from app.lifecycle import ClientLifecycle


def test_client_lifecycle_waits_for_grace_period_after_last_page_closes():
    now = [0.0]
    lifecycle = ClientLifecycle(idle_timeout=10, shutdown_grace=3, clock=lambda: now[0])
    lifecycle.heartbeat("page-1")

    now[0] = 1
    lifecycle.heartbeat("page-1", active=False)
    assert lifecycle.should_shutdown() is False

    now[0] = 3.9
    assert lifecycle.should_shutdown() is False
    now[0] = 4.0
    assert lifecycle.should_shutdown() is True


def test_client_lifecycle_does_not_shutdown_while_page_heartbeats_are_fresh():
    now = [0.0]
    lifecycle = ClientLifecycle(idle_timeout=10, shutdown_grace=3, clock=lambda: now[0])
    lifecycle.heartbeat("page-1")

    now[0] = 9
    lifecycle.heartbeat("page-1")
    now[0] = 18

    assert lifecycle.should_shutdown() is False


def test_client_lifecycle_shuts_down_after_a_page_stops_heartbeating():
    now = [0.0]
    lifecycle = ClientLifecycle(idle_timeout=10, shutdown_grace=3, clock=lambda: now[0])
    lifecycle.heartbeat("page-1")

    now[0] = 10.1
    assert lifecycle.should_shutdown() is False
    now[0] = 13.1
    assert lifecycle.should_shutdown() is True


def test_client_lifecycle_stays_idle_before_any_page_has_connected():
    now = [100.0]
    lifecycle = ClientLifecycle(idle_timeout=10, shutdown_grace=3, clock=lambda: now[0])

    now[0] = 1000.0

    assert lifecycle.should_shutdown() is False
