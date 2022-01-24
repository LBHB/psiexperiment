import pytest

import enaml
with enaml.imports():
    from .test_core_manifest import (PSITestContribution3,
                                     PSITestContribution2Manifest,
                                     PSITestContribution3Manifest
                                     )


from psi.core.enaml.api import PSIContribution, PSIManifest


class PSITestContribution1(PSIContribution):
    pass


class PSITestContribution2(PSIContribution):
    pass


def test_find_manifest():
    # Verify that it resturns the "base" manifest
    contribution = PSITestContribution1()
    assert contribution.find_manifest_class() == PSIManifest

    # Verify can find manifest in sidecar enaml file
    contribution = PSITestContribution2()
    assert contribution.find_manifest_class() == PSITestContribution2Manifest

    # Verify that manifest can be found in same enaml file as contribution
    contribution = PSITestContribution3()
    assert contribution.find_manifest_class() == PSITestContribution3Manifest
