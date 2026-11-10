from retail_core.policy import GatePolicy,Observation


def ready():
    p = GatePolicy(('chest','left_shoulder'),allow_synthetic=False)
    for camera in p.cameras:
        p.observe(camera,Observation(10.0,'CONTINUE',True,False,2.0,'epoch1'))
    return p


def test_stop_is_latched_and_continue_cannot_release():
    p = ready()
    p.enabled = True
    p.observe('chest',Observation(10.1,'STOP',True,False,2.0,'epoch1'))
    assert not p.permit(10.2,True,True,False)[0]
    p.observe('chest',Observation(10.2,'CONTINUE',True,False,2.0,'epoch1'))
    assert p.hold and not p.enabled
    assert not p.permit(10.3,True,True,False)[0]
    assert p.reset(10.3,True,True,True,False)[0]
    assert not p.enabled


def test_required_view_loss_does_not_become_empty():
    p = ready()
    p.enabled = True
    assert not p.permit(12.1,True,True,False)[0]
    assert p.hold
    p = ready()
    p.observations.pop('left_shoulder')
    assert not p.health(10.1)[0]


def test_unknown_and_invalid_are_not_clear():
    for action,valid in [('OBSERVE',True),('DATA_LOSS',False),('no_detcet',True),('CONTINUE',False)]:
        p = ready()
        p.enabled = True
        p.observe('chest',Observation(10.0,action,valid,False,2.0,'epoch1'))
        assert not p.permit(10.1,True,True,False)[0]


def test_restart_time_reversal_and_control_conflict_hold():
    for observation in [Observation(9.0,'CONTINUE',True,False,2.0,'epoch1'),
                        Observation(10.1,'CONTINUE',True,False,2.0,'epoch2')]:
        p = ready()
        p.observe('chest',observation)
        assert p.hold and not p.enabled
    p = ready()
    p.enabled = True
    assert not p.permit(10.1,True,True,True)[0]
    assert p.hold


def test_arm_and_feedback_interlocks():
    for stowed,fresh in [(False,True),(True,False)]:
        p = ready()
        p.enabled = True
        assert not p.permit(10.1,stowed,fresh,False)[0]
        assert p.hold
    p = ready()
    p.enabled,p.inhibited = True,True
    assert not p.permit(10.1,True,True,False)[0]


def test_synthetic_rejected_in_non_simulation_and_warning_is_distinct():
    p = ready()
    p.observe('chest',Observation(10.1,'NO_DETECT',True,True,2.0,'epoch1'))
    assert not p.health(10.2)[0]
    p = ready()
    p.observe('chest',Observation(10.1,'DANGER_WARNING',True,False,2.0,'epoch1'))
    assert p.health(10.2)==(True,'valid observations',True)


def test_reset_requires_feedback_and_never_enables():
    p = ready()
    p.stop('cancel')
    for stopped,stowed,fresh,conflict in [(False,True,True,False),(True,False,True,False),(True,True,False,False),(True,True,True,True)]:
        assert not p.reset(10.1,stopped,stowed,fresh,conflict)[0]
        assert p.hold
    assert p.reset(10.1,True,True,True,False)[0]
    assert not p.hold and not p.enabled

