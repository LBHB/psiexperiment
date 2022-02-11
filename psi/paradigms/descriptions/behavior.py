from psi.experiment.api import ParadigmDescription


PATH = 'psi.paradigms.behavior.'
CORE_PATH = 'psi.paradigms.core.'


ParadigmDescription(
    'appetitive_gonogo_food', 'Appetitive GO-NOGO food', 'animal', [
        (PATH + 'behavior_np_gonogo.BehaviorManifest',),
        (PATH + 'behavior_mixins.PelletDispenserMixinManifest',),
    ],
)


ParadigmDescription(
    'auto_gonogo', 'Auto GO-NOGO', 'animal', [
        (PATH + 'behavior_mixins.BaseGoNogoMixin',),
        (PATH + 'behavior_auto_gonogo.BehaviorManifest',),
        (CORE_PATH + 'microphone_mixins.MicrophoneSignalViewManifest',),
        (CORE_PATH + 'microphone_mixins.MicrophoneFFTViewManifest',),
    ],
)
