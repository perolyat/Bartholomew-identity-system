"""
Stage 1, S1.6: host-device onboarding guidance -- static content.

See docs/S1_6_HOST_DEVICE_ONBOARDING_DESIGN.md (approved 2026-08-05) for the
design this implements verbatim. Fixed editorial content, not user-tunable
runtime config, so it lives here as a plain Python module (covered by the
same ruff/black/review process as the rest of the codebase) rather than a
YAML/JSON file under bartholomew/config/.

Every string below is written to satisfy the design's two non-negotiable
invariants (design doc Sec 9):
- no target is presented as the only supported option, or visually/
  structurally privileged over the others (design doc Sec 7) -- enforced
  here by giving every DeploymentTarget the same shape and the CHOOSING_GUIDE
  table five priority-conditional rows rather than one verdict;
- no planned-but-unbuilt capability (cross-device sync, a hosted runtime,
  data export) is described as available today -- every `upgrade_path`
  below that depends on Stage 6/data-portability work says so explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DeploymentTarget:
    target_id: str
    name: str
    feel: str
    advantages: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    upgrade_path: str = ""
    available_today: bool = True

    def to_dict(self) -> dict:
        return {
            "target_id": self.target_id,
            "name": self.name,
            "feel": self.feel,
            "advantages": self.advantages,
            "limitations": self.limitations,
            "upgrade_path": self.upgrade_path,
            "available_today": self.available_today,
        }


@dataclass(frozen=True)
class ChoosingGuideEntry:
    priority: str
    target_id: str
    rationale: str

    def to_dict(self) -> dict:
        return {
            "priority": self.priority,
            "target_id": self.target_id,
            "rationale": self.rationale,
        }


# Order matches ROADMAP.md's own listing exactly (design doc Sec 3) --
# local-device-first-then-hybrid, not alphabetical, not a ranking.
DEPLOYMENT_TARGETS: list[DeploymentTarget] = [
    DeploymentTarget(
        target_id="phone",
        name="Phone",
        feel=(
            "Bartholomew lives in your pocket. Notifications, nudges, and check-ins arrive the "
            "way any other app's would; voice becomes the natural way to talk to it once voice "
            "support exists. Day to day, this is the most ambient of the five deployment "
            "options -- Bartholomew is simply wherever you already are."
        ),
        advantages=[
            "Always with you.",
            "Natural fit for talking to Bartholomew directly, once voice support exists.",
            "Uses the phone's own microphone and notifications -- no extra hardware needed.",
        ],
        limitations=[
            "Running the AI models locally is real work for a phone's battery and processor.",
            "Phone operating systems restrict how long background apps can keep running, which "
            "works against an always-on assistant and its proactive check-ins.",
            "The least storage of the five options for a memory that keeps growing over time.",
        ],
        upgrade_path=(
            "A phone is a reasonable place to start -- small footprint, nothing else to set up. "
            "If storage, battery drain, or background-reliability limits become noticeable, the "
            "intended path is to move the trusted runtime to a personal computer or home "
            "server/hub while keeping the phone as a client, without losing memory or governance "
            "history in the move. That migration path (cross-device access, data export) is not "
            "built yet -- see the note on planned-but-unbuilt capability below."
        ),
        available_today=True,
    ),
    DeploymentTarget(
        target_id="personal_computer",
        name="Personal computer",
        feel=(
            "Bartholomew runs quietly in the background on the machine you already work at, at "
            'its most capable of any single-device option. It feels less "always in your '
            'pocket" and more "always at your desk," ready the moment you sit down.'
        ),
        advantages=[
            "The best balance of processing power and battery life of the five options for "
            "running the AI models well.",
            "An always-on assistant is straightforward to set up and keep running.",
            "The most storage headroom of any single-device option.",
            "The best fit for deeper file-system and OS integration.",
        ],
        limitations=[
            "Not always with you -- away from the machine, there is no way yet to reach the "
            "assistant running on it.",
            "You need to keep it powered on for scheduled check-ins and proactive suggestions "
            "to fire on time.",
        ],
        upgrade_path=(
            'Works well as a permanent home for the trusted runtime on its own. If "not always '
            'with me" becomes the limitation that matters, the intended path -- once '
            "cross-device access is built -- is for this machine to stay the authoritative "
            "runtime while a phone or other devices become reachable clients of it, with no "
            "need to start over. That cross-device capability is not built yet."
        ),
        available_today=True,
    ),
    DeploymentTarget(
        target_id="home_server_hub",
        name="Home server/hub",
        feel=(
            "Bartholomew becomes infrastructure -- always on, and in principle reachable from "
            "every device in the home, the way a home router or network storage device already "
            'is. Less "an app you open" and more "a presence in the house."'
        ),
        advantages=[
            "Always-on without depending on a personal device's battery or the user remembering "
            "to leave a laptop open.",
            "Centralizes one authoritative assistant that, once cross-device access is built, "
            "could serve multiple client devices from a single trusted source.",
            "The best fit for a genuinely multi-device household setup.",
            "Of the five, the one whose day-to-day feel changes least over time, since it isn't "
            "competing for the same battery or attention as a phone or laptop.",
        ],
        limitations=[
            "Requires you to own, set up, and maintain a machine that stays powered on -- a "
            "real cost and technical-complexity barrier, not assumed to be free.",
            "The trusted runtime is now physically separate from whatever device you're "
            "actually holding, so reaching it while away depends entirely on cross-device work "
            "that does not exist yet.",
        ],
        upgrade_path=(
            "This is close to the long-run destination for someone who wants an always-on, "
            "privacy-preserving assistant and is willing to invest in it -- there usually isn't "
            'a further "upgrade" from here beyond making it reachable from more devices, which '
            "is a capability arriving to this target, not a reason to leave it."
        ),
        available_today=True,
    ),
    DeploymentTarget(
        target_id="hosted_cloud_service",
        name="Hosted cloud service",
        feel=(
            "From your side, likely the lowest-friction of the five -- no device to dedicate, "
            "no assistant process to keep running, sign in from anywhere. That convenience is "
            "real and worth stating plainly, even though this project's own architecture "
            "deliberately does not offer this as a first-party option today."
        ),
        advantages=[
            "No local hardware constraints on how good the AI's responses can be.",
            "In principle the easiest option for reaching your assistant from anywhere, since a "
            "cloud endpoint doesn't depend on any one physical device being on.",
            "The least setup effort of the five options.",
        ],
        limitations=[
            "This project deliberately keeps sensitive memory and safety controls -- including "
            "the emergency shutdown -- under your own local control rather than a remote "
            "service's, because a remote-hosted safety control is a remote service's uptime and "
            "trustworthiness, not yours.",
            "This project does not offer a first-party hosted deployment of the trusted "
            "assistant itself today -- only optional use of cloud AI providers for individual "
            "responses, from a local deployment.",
        ],
        upgrade_path=(
            'Because no first-party hosted assistant exists, there is no "start here, migrate '
            'later" path to describe for this option the way there is for the other four. If '
            "pure convenience is what matters most to you, hybrid local-plus-cloud (below) is "
            "the option that comes closest today, while keeping your local control intact."
        ),
        available_today=False,
    ),
    DeploymentTarget(
        target_id="hybrid_local_plus_cloud",
        name="Hybrid local-plus-cloud",
        feel=(
            "Day to day, indistinguishable from whichever local option (phone, computer, or "
            "hub) is hosting the runtime -- the difference is invisible in normal use and shows "
            "up only when Bartholomew reaches for an optional cloud AI provider for a harder "
            "task you've explicitly allowed, or, once built, when a device other than the host "
            "becomes reachable too."
        ),
        advantages=[
            "This is the architecture this project actually builds toward: a local device or "
            "hub stays in control of sensitive memory, safety controls, and the emergency "
            "shutdown -- independent of network or cloud availability -- while optional cloud "
            "AI services are used only where you explicitly opt in.",
            "Keeps your local control while allowing better AI response quality than local-only "
            "processing power permits on its own.",
        ],
        limitations=[
            "More moving parts to understand than picking a single option.",
            'Today, "hybrid" concretely means "a local deployment plus optional cloud AI '
            'calls you opt into" -- the richer multi-device syncing that the word "hybrid" '
            "might suggest is not built yet.",
        ],
        upgrade_path=(
            "Not a starting point most people pick first -- it presumes an existing local "
            "deployment to pair with cloud services -- but the natural place any of the other "
            "three local options (phone, computer, or hub) can grow into once you want better "
            "AI response quality or, later, genuine multi-device reach, with no loss of the "
            "local control that made the local option worth choosing in the first place."
        ),
        available_today=True,
    ),
]


# Design doc Sec 4: priority-conditional guidance, never a single verdict --
# five distinct priorities mapped to five distinct targets, each with its
# own reasoning (design doc Sec 7's neutrality-enforcement mechanism).
CHOOSING_GUIDE: list[ChoosingGuideEntry] = [
    ChoosingGuideEntry(
        priority="Convenience and mobility",
        target_id="phone",
        rationale="Always with you; nothing extra to set up or maintain.",
    ),
    ChoosingGuideEntry(
        priority="Everyday desktop use",
        target_id="personal_computer",
        rationale=(
            "The best AI response quality and storage of the single-device options, "
            "while you're at it."
        ),
    ),
    ChoosingGuideEntry(
        priority="Privacy and an always-on presence",
        target_id="home_server_hub",
        rationale=(
            "Stays on independent of any one personal device; the closest fit to a fully "
            "local, always-reachable setup."
        ),
    ),
    ChoosingGuideEntry(
        priority="Minimal setup effort",
        target_id="hosted_cloud_service",
        rationale=(
            "The lowest friction to get started -- with the honest caveat that this project "
            "does not offer a first-party hosted assistant today, only optional cloud AI calls "
            "from a local deployment."
        ),
    ),
    ChoosingGuideEntry(
        priority="The long-term ideal, once available",
        target_id="hybrid_local_plus_cloud",
        rationale=(
            "The architecture this project actually builds toward: local control plus optional "
            "cloud quality -- most valuable once multi-device syncing lands, but already "
            "meaningful today via optional cloud AI calls from any local deployment."
        ),
    ),
]


# Design doc Sec 4/9: must appear prominently, not as a footnote, wherever
# the choosing-guide table is shown -- in both the UI and this API response.
MIGRATION_NOTE = (
    "This choice is not permanent. Every option above has a future upgrade path because "
    "migrating later is expected, not a failure to plan ahead -- someone who starts on a phone "
    "for convenience and later wants an always-on home-server feel, or wants to add optional "
    "cloud AI calls, is not locked into their first answer."
)
