"""
Diablo 4 Paragon Board enums, grid constants, class stat priorities, and official
in-game board, glyph, and node metadata extracted from DiabloTools/d4data.
"""

import json
import os
from enum import IntEnum, StrEnum

DEFAULT_META_DATA_PATH = os.path.join(
    os.path.dirname(os.path.realpath(__file__)),
    "paragon_meta_data.json",
)


def load_meta_data(path: str | None = None) -> dict:
    """Load structural board metadata from `paragon_meta_data.json`."""
    target = path or DEFAULT_META_DATA_PATH
    if not os.path.exists(target) and path and path.endswith(os.path.join("paragon_optimizer", "paragon_meta_data.json")):
        target = DEFAULT_META_DATA_PATH
    with open(target, "r", encoding="utf-8") as f:
        return json.load(f)


class PARAGON_EXIT(IntEnum):
    TOP = 0
    RIGHT = 1
    BOTTOM = 2
    LEFT = 3


EXIT_COORDS = {
    PARAGON_EXIT.TOP: (0, 10),
    PARAGON_EXIT.RIGHT: (10, 20),
    PARAGON_EXIT.BOTTOM: (20, 10),
    PARAGON_EXIT.LEFT: (10, 0),
}

STARTER_START_COORD = (14, 10)
MAX_PARAGON_POINTS = 342

# Screen-space coordinate deltas (x increases RIGHT, y increases BOTTOM)
DIR_DELTA = {
    PARAGON_EXIT.TOP: (0, -1),
    PARAGON_EXIT.RIGHT: (1, 0),
    PARAGON_EXIT.BOTTOM: (0, 1),
    PARAGON_EXIT.LEFT: (-1, 0),
}

OPPOSITE_EXIT = {
    PARAGON_EXIT.TOP: PARAGON_EXIT.BOTTOM,
    PARAGON_EXIT.RIGHT: PARAGON_EXIT.LEFT,
    PARAGON_EXIT.BOTTOM: PARAGON_EXIT.TOP,
    PARAGON_EXIT.LEFT: PARAGON_EXIT.RIGHT,
}


class NODE_TYPE(StrEnum):
    LEGENDARY = "LEGENDARY"
    GLYPH = "GLYPH"
    EXIT = "EXIT"
    RARE = "RARE"
    MAGIC = "MAGIC"
    INT = "INT"
    WILL = "WILL"
    DEX = "DEX"
    STR = "STR"
    MAGIC_INT = "MAGIC_INT"
    MAGIC_WILL = "MAGIC_WILL"
    MAGIC_DEX = "MAGIC_DEX"
    MAGIC_STR = "MAGIC_STR"
    DEAD = "DEAD"


class D4_CLASS(StrEnum):
    NECRO = "Necro"
    BARB = "Barb"
    DRUID = "Druid"
    ROGUE = "Rogue"
    SORC = "Sorc"
    SPIRIT = "Spirit"
    PALADIN = "Paladin"
    WARLOCK = "Warlock"


# Class node priorities aligned with each class's Primary Stat, two Secondary Stats
# (which have MAGIC_<STAT> clusters and unlock Rare node bonus thresholds), and Off-Stat (0.0).
# Note: d4-paragon.com's bytecode had a bug on ROGUE (setting STR=0, WILL=0.025 despite Rogue
# boards having 20 MAGIC_STR nodes and 0 MAGIC_WILL nodes) and copy-pasted BARB/DRUID onto
# PALADIN/WARLOCK (setting INT=0 on Paladin and STR=0 on Warlock).
def _build_class_priority(prim: NODE_TYPE, sec1: NODE_TYPE, sec2: NODE_TYPE) -> dict[NODE_TYPE, float]:
    d = {nt: 0.0 for nt in NODE_TYPE}
    d.update({NODE_TYPE.MAGIC: 0.6, NODE_TYPE.RARE: 1.0, prim: 0.5, sec1: 0.03, sec2: 0.025})
    for nt, val in ((prim, 0.7), (sec1, 0.042), (sec2, 0.035)):
        d[NODE_TYPE[f"MAGIC_{nt.value}"]] = val
    return d


DEFAULT_CLASS_NODE_PRIORITY = {
    cls: _build_class_priority(prim, sec1, sec2)
    for cls, (prim, sec1, sec2) in {
        D4_CLASS.BARB: (NODE_TYPE.STR, NODE_TYPE.DEX, NODE_TYPE.WILL),
        D4_CLASS.DRUID: (NODE_TYPE.WILL, NODE_TYPE.DEX, NODE_TYPE.INT),
        D4_CLASS.NECRO: (NODE_TYPE.INT, NODE_TYPE.DEX, NODE_TYPE.WILL),
        D4_CLASS.ROGUE: (NODE_TYPE.DEX, NODE_TYPE.INT, NODE_TYPE.STR),
        D4_CLASS.SORC: (NODE_TYPE.INT, NODE_TYPE.DEX, NODE_TYPE.WILL),
        D4_CLASS.SPIRIT: (NODE_TYPE.DEX, NODE_TYPE.STR, NODE_TYPE.INT),
        D4_CLASS.PALADIN: (NODE_TYPE.STR, NODE_TYPE.INT, NODE_TYPE.WILL),
        D4_CLASS.WARLOCK: (NODE_TYPE.WILL, NODE_TYPE.STR, NODE_TYPE.INT),
    }.items()
}

STAT_ALIASES = {
    "DEX": (NODE_TYPE.DEX, NODE_TYPE.MAGIC_DEX),
    "DEXTERITY": (NODE_TYPE.DEX, NODE_TYPE.MAGIC_DEX),
    "INT": (NODE_TYPE.INT, NODE_TYPE.MAGIC_INT),
    "INTELLIGENCE": (NODE_TYPE.INT, NODE_TYPE.MAGIC_INT),
    "STR": (NODE_TYPE.STR, NODE_TYPE.MAGIC_STR),
    "STRENGTH": (NODE_TYPE.STR, NODE_TYPE.MAGIC_STR),
    "WILL": (NODE_TYPE.WILL, NODE_TYPE.MAGIC_WILL),
    "WILLPOWER": (NODE_TYPE.WILL, NODE_TYPE.MAGIC_WILL),
    "ALL": (
        NODE_TYPE.DEX,
        NODE_TYPE.INT,
        NODE_TYPE.STR,
        NODE_TYPE.WILL,
        NODE_TYPE.MAGIC_DEX,
        NODE_TYPE.MAGIC_INT,
        NODE_TYPE.MAGIC_STR,
        NODE_TYPE.MAGIC_WILL,
    ),
    "MAGIC": (NODE_TYPE.MAGIC, NODE_TYPE.MAGIC_DEX, NODE_TYPE.MAGIC_INT, NODE_TYPE.MAGIC_STR, NODE_TYPE.MAGIC_WILL),
    "RARE": (NODE_TYPE.RARE,),
}

# Official in-game Diablo 4 Paragon Board names mapped from StringList/ParagonBoard_<ID>.stl
BOARD_NAMES: dict[str, str] = {
    # Barbarian
    "Paragon_Barb_00": "Start",
    "Paragon_Barb_01": "Hemorrhage",
    "Paragon_Barb_02": "Blood Rage",
    "Paragon_Barb_03": "Carnage",
    "Paragon_Barb_04": "Decimator",
    "Paragon_Barb_05": "Bone Breaker",
    "Paragon_Barb_06": "Flawless Technique",
    "Paragon_Barb_07": "Warbringer",
    "Paragon_Barb_08": "Weapons Master",
    "Paragon_Barb_10": "Force of Nature",
    # Druid
    "Paragon_Druid_00": "Start",
    "Paragon_Druid_01": "Thunderstruck",
    "Paragon_Druid_02": "Earthen Devastation",
    "Paragon_Druid_03": "Survival Instincts",
    "Paragon_Druid_04": "Lust for Carnage",
    "Paragon_Druid_05": "Heightened Malice",
    "Paragon_Druid_06": "Inner Beast",
    "Paragon_Druid_07": "Constricting Tendrils",
    "Paragon_Druid_08": "Ancestral Guidance",
    "Paragon_Druid_10": "Untamed",
    # Necromancer
    "Paragon_Necro_00": "Start",
    "Paragon_Necro_01": "Cult Leader",
    "Paragon_Necro_02": "Hulking Monstrosity",
    "Paragon_Necro_03": "Flesh-eater",
    "Paragon_Necro_04": "Scent of Death",
    "Paragon_Necro_05": "Bone Graft",
    "Paragon_Necro_06": "Blood Begets Blood",
    "Paragon_Necro_07": "Bloodbath",
    "Paragon_Necro_08": "Wither",
    "Paragon_Necro_10": "Frailty",
    # Paladin
    "Paragon_Paladin_00": "Start",
    "Paragon_Paladin_01": "Castle",
    "Paragon_Paladin_02": "Shield Bearer",
    "Paragon_Paladin_03": "Fervent",
    "Paragon_Paladin_04": "Preacher",
    "Paragon_Paladin_05": "Divinity",
    "Paragon_Paladin_06": "Relentless",
    "Paragon_Paladin_07": "Sentencing",
    "Paragon_Paladin_08": "Endure",
    "Paragon_Paladin_09": "Beacon",
    # Rogue
    "Paragon_Rogue_00": "Start",
    "Paragon_Rogue_01": "Eldritch Bounty",
    "Paragon_Rogue_02": "Tricks of the Trade",
    "Paragon_Rogue_03": "Cheap Shot",
    "Paragon_Rogue_04": "Deadly Ambush",
    "Paragon_Rogue_05": "Leyrana's Instinct",
    "Paragon_Rogue_06": "No Witnesses",
    "Paragon_Rogue_07": "Exploit Weakness",
    "Paragon_Rogue_08": "Cunning Stratagem",
    "Paragon_Rogue_10": "Danse Macabre",
    # Sorcerer
    "Paragon_Sorc_00": "Start",
    "Paragon_Sorc_01": "Searing Heat",
    "Paragon_Sorc_02": "Frigid Fate",
    "Paragon_Sorc_03": "Static Surge",
    "Paragon_Sorc_04": "Elemental Summoner",
    "Paragon_Sorc_05": "Burning Instinct",
    "Paragon_Sorc_06": "Icefall",
    "Paragon_Sorc_07": "Ceaseless Conduit",
    "Paragon_Sorc_08": "Enchantment Master",
    "Paragon_Sorc_10": "Fundamental Release",
    # Spiritborn
    "Paragon_Spirit_0": "Start",
    "Paragon_Spirit_00": "Start",
    "Paragon_Spirit_01": "In-Fighter",
    "Paragon_Spirit_02": "Spiney Skin",
    "Paragon_Spirit_03": "Viscous Shield",
    "Paragon_Spirit_04": "Bitter Medicine",
    "Paragon_Spirit_05": "Revealing",
    "Paragon_Spirit_06": "Drive",
    "Paragon_Spirit_07": "Convergence",
    "Paragon_Spirit_08": "Prodigy's Tempo",
    "Paragon_Spirit_10": "Swarm of Storms",
    # Warlock
    "Paragon_Warlock_00": "Start",
    "Paragon_Warlock_01": "Fathomless",
    "Paragon_Warlock_02": "Demonic Spicules",
    "Paragon_Warlock_03": "Dynamism",
    "Paragon_Warlock_04": "Pyrosis",
    "Paragon_Warlock_05": "Overmind",
    "Paragon_Warlock_06": "Greater Hex",
    "Paragon_Warlock_07": "Ritualism",
    "Paragon_Warlock_08": "Chaos",
    "Paragon_Warlock_10": "Dominion",
}

# Official Diablo 4 Glyph metadata extracted from DiabloTools/d4data:
#   glyph_id -> (in_game_name, governed_stat, threshold_required_in_radius, primary_scaling_mode)
# where `primary_scaling_mode` is:
#   - 'stat':   primary bonus scales per point of governed core stat in radius (+5 normal, +7 magic, +10 rare)
#   - 'normal': primary bonus boosts all Normal stat nodes in radius by +200%+ (e.g. Headhunter, Eliminator, Challenger)
#   - 'magic':  primary bonus boosts all Magic nodes in radius by +250%..300%+ (e.g. Unleash, Versatility, Fluidity)
#   - 'rare':   primary bonus boosts all Rare nodes in radius by +200%..250%+ (e.g. Tactician, Protector, Dominate)
GLYPH_DATA: dict[str, tuple[str, str, int, str]] = {
    "Rare_001_Intelligence_Main": ("Enchanter", "INT", 40, "stat"),
    "Rare_002_Intelligence_Main": ("Unleash", "INT", 40, "magic"),
    "Rare_003_Intelligence_Main": ("Elementalist", "INT", 40, "rare"),
    "Rare_004_Intelligence_Main": ("Adept", "INT", 40, "stat"),
    "Rare_005_Intelligence_Main": ("Conjurer", "INT", 40, "stat"),
    "Rare_006_Intelligence_Main": ("Charged", "INT", 40, "stat"),
    "Rare_007_Willpower_Side": ("Torch", "WILL", 25, "stat"),
    "Rare_008_Willpower_Side": ("Pyromaniac", "WILL", 25, "rare"),
    "Rare_009_Willpower_Side": ("Cryopathy", "WILL", 25, "stat"),
    "Rare_010_Dexterity_Main": ("Tactician", "DEX", 25, "rare"),
    "Rare_011_Intelligence_Side": ("Guzzler", "INT", 25, "stat"),
    "Rare_011_Willpower_Side": ("Imbiber", "WILL", 25, "stat"),
    "Rare_012_Intelligence_Side": ("Protector", "INT", 25, "rare"),
    "Rare_012_Willpower_Side": ("Reinforced", "WILL", 25, "rare"),
    "Rare_013_Dexterity_Side": ("Poise", "DEX", 25, "rare"),
    "Rare_014_Dexterity_Side": ("Territorial", "DEX", 25, "stat"),
    "Rare_014_Strength_Main": ("Turf", "STR", 40, "stat"),
    "Rare_014_Strength_Side": ("Turf", "STR", 25, "stat"),
    "Rare_015_Dexterity_Side": ("Flamefeeder", "DEX", 25, "stat"),
    "Rare_016_Dexterity_Side": ("Exploit", "DEX", 25, "stat"),
    "Rare_016_Intelligence_Side": ("Exploit", "INT", 25, "stat"),
    "Rare_016_Strength_Side": ("Exploit", "STR", 25, "stat"),
    "Rare_017_Dexterity_Side": ("Winter", "DEX", 25, "rare"),
    "Rare_018_Dexterity_Side": ("Electrocute", "DEX", 25, "rare"),
    "Rare_019_Dexterity_Side": ("Destruction", "DEX", 25, "stat"),
    "Rare_020_Dexterity_Side": ("Control", "DEX", 25, "stat"),
    "Rare_020_Intelligence_Main": ("Control", "INT", 40, "stat"),
    "Rare_020_Intelligence_Side": ("Control", "INT", 25, "stat"),
    "Rare_021_Strength_Main": ("Ambidextrous", "STR", 40, "magic"),
    "Rare_022_Strength_Main": ("Might", "STR", 40, "magic"),
    "Rare_023_Strength_Main": ("Cleaver", "STR", 40, "stat"),
    "Rare_024_Strength_Main": ("Seething", "STR", 40, "stat"),
    "Rare_025_Strength_Main": ("Crusher", "STR", 40, "stat"),
    "Rare_026_Strength_Main": ("Executioner", "STR", 40, "stat"),
    "Rare_027_Strength_Main": ("Ire", "STR", 40, "stat"),
    "Rare_028_Strength_Main": ("Marshal", "STR", 40, "magic"),
    "Rare_029_Dexterity_Side": ("Bloodfeeder", "DEX", 25, "stat"),
    "Rare_030_Dexterity_Side": ("Wrath", "DEX", 25, "stat"),
    "Rare_031_Dexterity_Side": ("Weapon Master", "DEX", 25, "rare"),
    "Rare_032_Dexterity_Side": ("Mortal Draw", "DEX", 25, "stat"),
    "Rare_033_Intelligence_Side": ("Revenge", "INT", 25, "rare"),
    "Rare_033_Willpower_Side": ("Revenge", "WILL", 25, "rare"),
    "Rare_033_Willpower_Side_Necro": ("Revenge", "WILL", 25, "rare"),
    "Rare_034_Intelligence_Side": ("Undaunted", "INT", 25, "stat"),
    "Rare_034_Willpower_Side": ("Undaunted", "WILL", 25, "stat"),
    "Rare_035_Intelligence_Side": ("Dominate", "INT", 25, "rare"),
    "Rare_035_Willpower_Side": ("Dominate", "WILL", 25, "stat"),
    "Rare_035_Willpower_Side_Necro": ("Dominate", "WILL", 25, "stat"),
    "Rare_036_Willpower_Side": ("Disembowel", "WILL", 25, "stat"),
    "Rare_037_Willpower_Side": ("Brawl", "WILL", 25, "rare"),
    "Rare_038_Intelligence_Main": ("Corporeal", "INT", 40, "magic"),
    "Rare_039_Willpower_Main": ("Fang and Claw", "WILL", 40, "magic"),
    "Rare_040_Willpower_Main": ("Earth and Sky", "WILL", 40, "magic"),
    "Rare_041_Intelligence_Side": ("Wilds", "INT", 25, "stat"),
    "Rare_042_Willpower_Main": ("Werebear", "WILL", 40, "stat"),
    "Rare_043_Willpower_Main": ("Werewolf", "WILL", 40, "stat"),
    "Rare_044_Willpower_Main": ("Human", "WILL", 40, "stat"),
    "Rare_045_Intelligence_Side": ("Bane", "INT", 25, "stat"),
    "Rare_045_Strength_Side": ("Bane", "STR", 25, "stat"),
    "Rare_046_Dexterity_Side": ("Abyssal", "DEX", 25, "rare"),
    "Rare_046_Intelligence_Side": ("Keeper", "INT", 25, "rare"),
    "Rare_047_Dexterity_Side": ("Fulminate", "DEX", 25, "stat"),
    "Rare_047_Intelligence_Side": ("Fulminate", "DEX", 25, "stat"),
    "Rare_048_Dexterity_Side": ("Tracker", "DEX", 25, "stat"),
    "Rare_048_Intelligence_Side": ("Tracker", "INT", 25, "stat"),
    "Rare_049_Dexterity_Side": ("Outmatch", "DEX", 25, "rare"),
    "Rare_049_Strength_Main": ("Outmatch", "STR", 40, "rare"),
    "Rare_049_Strength_Side": ("Outmatch", "STR", 25, "rare"),
    "Rare_050_Dexterity_Main": ("Spirit", "DEX", 40, "stat"),
    "Rare_050_Dexterity_Side": ("Spirit", "DEX", 25, "stat"),
    "Rare_050_Willpower_Side": ("Spirit", "WILL", 25, "stat"),
    "Rare_051_Dexterity_Side": ("Shapeshifter", "DEX", 25, "rare"),
    "Rare_052_Dexterity_Main": ("Versatility", "DEX", 40, "magic"),
    "Rare_053_Dexterity_Main": ("Closer", "DEX", 40, "stat"),
    "Rare_054_Dexterity_Main": ("Ranger", "DEX", 40, "stat"),
    "Rare_055_Dexterity_Main": ("Chip", "DEX", 40, "stat"),
    "Rare_055_Dexterity_Side": ("Chip", "DEX", 25, "stat"),
    "Rare_055_Willpower_Side": ("Chip", "WILL", 25, "stat"),
    "Rare_056_Dexterity_Main": ("Grenadier", "DEX", 40, "stat"),
    "Rare_057_Dexterity_Main": ("Fluidity", "DEX", 40, "magic"),
    "Rare_058_Intelligence_Side": ("Infusion", "INT", 25, "stat"),
    "Rare_059_Dexterity_Main": ("Devious", "DEX", 40, "magic"),
    "Rare_060_Dexterity_Side": ("Warrior", "DEX", 25, "stat"),
    "Rare_061_Intelligence_Side": ("Combat", "INT", 25, "stat"),
    "Rare_062_Dexterity_Side": ("Gravekeeper", "DEX", 25, "rare"),
    "Rare_063_Intelligence_Side": ("Canny", "INT", 25, "stat"),
    "Rare_064_Intelligence_Side": ("Efficacy", "INT", 25, "rare"),
    "Rare_065_Intelligence_Side": ("Snare", "INT", 25, "stat"),
    "Rare_066_Dexterity_Side": ("Essence", "DEX", 25, "stat"),
    "Rare_067_Strength_Side": ("Pride", "STR", 25, "rare"),
    "Rare_068_Strength_Side": ("Ambush", "STR", 25, "stat"),
    "Rare_069_Intelligence_Main": ("Sacrificial", "INT", 40, "magic"),
    "Rare_070_Intelligence_Main": ("Blood-drinker", "INT", 40, "magic"),
    "Rare_071_Intelligence_Main": ("Deadraiser", "INT", 40, "stat"),
    "Rare_072_Intelligence_Main": ("Mage", "INT", 40, "stat"),
    "Rare_073_Intelligence_Main": ("Amplify", "INT", 40, "magic"),
    "Rare_074_Willpower_Side": ("Golem", "WILL", 25, "stat"),
    "Rare_075_Willpower_Side": ("Scourge", "INT", 40, "stat"),
    "Rare_076_Strength_Main": ("Diminish", "STR", 40, "rare"),
    "Rare_076_Strength_Side": ("Diminish", "STR", 25, "rare"),
    "Rare_077_Willpower_Side": ("Warding", "WILL", 25, "rare"),
    "Rare_078_Willpower_Side": ("Darkness", "INT", 40, "stat"),
    "Rare_079_Dexterity_Side": ("Exploit", "DEX", 25, "stat"),
    "Rare_080_Strength_Main": ("Twister", "STR", 40, "stat"),
    "Rare_081_Strength_Main": ("Rumble", "STR", 40, "stat"),
    "Rare_082_Dexterity_Main": ("Explosive", "DEX", 40, "stat"),
    "Rare_083_Intelligence_Side": ("Nightstalker", "INT", 25, "stat"),
    "Rare_084_Intelligence_Main": ("Stalagmite", "INT", 40, "stat"),
    "Rare_085_Dexterity_Side": ("Invocation", "DEX", 25, "stat"),
    "Rare_086_Dexterity_Side": ("Tectonic", "DEX", 25, "stat"),
    "Rare_087_Willpower_Main": ("Electrocution", "WILL", 40, "stat"),
    "Rare_088_Intelligence_Main": ("Exhumation", "INT", 40, "stat"),
    "Rare_089_Willpower_Side": ("Desecration", "WILL", 25, "stat"),
    "Rare_090_Dexterity_Main": ("Menagerist", "DEX", 40, "magic"),
    "Rare_091_Strength_Side": ("Hone", "STR", 25, "stat"),
    "Rare_092_Intelligence_Side": ("Consumption", "INT", 25, "stat"),
    "Rare_093_Dexterity_Main": ("Fitness", "DEX", 40, "stat"),
    "Rare_094_Intelligence_Side": ("Ritual", "INT", 25, "stat"),
    "Rare_095_Dexterity_Main": ("Jagged Plume", "DEX", 40, "stat"),
    "Rare_096_Strength_Side": ("Innate", "STR", 25, "stat"),
    "Rare_097_Dexterity_Main": ("Wildfire", "DEX", 40, "stat"),
    "Rare_098_Strength_Side": ("Colossal", "STR", 25, "stat"),
    "Rare_100_Dexterity_Main": ("Talon", "DEX", 40, "stat"),
    "Rare_101_Strength_Side": ("Hubris", "STR", 25, "magic"),
    "Rare_102_Dexterity_Main": ("Fester", "DEX", 40, "stat"),
    "Rare_103_Strength_Main": ("Sentinel", "STR", 40, "stat"),
    "Rare_104_Dexterity_Side": ("Honed", "INT", 25, "stat"),
    "Rare_105_Strength_Main": ("Law", "STR", 40, "stat"),
    "Rare_106_Strength_Main": ("Retribution", "STR", 40, "rare"),
    "Rare_106_Willpower_Side": ("Arbiter", "WILL", 25, "stat"),
    "Rare_107_Strength_Main": ("Resplendence", "STR", 40, "magic"),
    "Rare_108_Intelligence_Side": ("Judicator", "INT", 25, "stat"),
    "Rare_109_Dexterity_Side": ("Feverous", "WILL", 25, "stat"),
    "Rare_110_Strength_Main": ("Apostle", "STR", 40, "stat"),
    "Rare_111_Willpower_Main": ("Blood Frenzy", "WILL", 40, "stat"),
    "Rare_112_Willpower_Main": ("Eliminator", "WILL", 40, "normal"),
    "Rare_113_Willpower_Main": ("Death Aura", "WILL", 40, "rare"),
    "Rare_114_Intelligence_Side": ("Occultist", "INT", 25, "stat"),
    "Rare_115_Intelligence_Side": ("Unbound", "INT", 25, "magic"),
    "Rare_116_Intelligence_Side": ("Hellforge", "INT", 25, "stat"),
    "Rare_117_Strength_Side": ("Abyssal", "WILL", 40, "stat"),
    "Rare_118_Willpower_Main": ("Ichor Carapace", "WILL", 40, "stat"),
    "Rare_119_Strength_Side": ("Wrath", "WILL", 40, "stat"),
    "Rare_120_Willpower_Main": ("Eldritch Sight", "WILL", 40, "stat"),
    "Rare_121_Intelligence_Side": ("Entropy", "INT", 25, "stat"),
    "Rare_122_Willpower_Main": ("Vanguard", "WILL", 40, "stat"),
    "Rare_123_Willpower_Main": ("Mastermind", "WILL", 40, "stat"),
    "Rare_124_Willpower_Main": ("Archfiend", "WILL", 40, "stat"),
    "Rare_125_Willpower_Main": ("Control", "WILL", 40, "stat"),
    "Rare_126_Strength_Side": ("Destruction", "WILL", 40, "stat"),
    "Rare_127_Intelligence_Side": ("Attrition", "INT", 25, "rare"),
    "Rare_128_Strength_Side": ("Demonologist", "WILL", 40, "stat"),
    "Rare_129_Strength_Side": ("Empowered", "WILL", 40, "stat"),
    "Rare_130_Dexterity_Main": ("Volley", "DEX", 40, "stat"),
    "Rare_131_Intelligence_Side": ("Eclipse", "INT", 25, "stat"),
    "Rare_132_Strength_Side": ("Assassin", "STR", 25, "stat"),
    "Rare_133_Intelligence_Side": ("Superiority", "INT", 25, "magic"),
    "Rare_Dex_Generic": ("Headhunter", "DEX", 40, "normal"),
    "Rare_Int_Generic": ("Eliminator", "INT", 40, "normal"),
    "Rare_Str_Generic": ("Challenger", "STR", 40, "normal"),
    "Rare_Will_Generic": ("Headhunter", "WILL", 40, "normal"),
    "S02_Rare_Vampiric_01": ("Tears of Blood", "ALL", 50, "stat"),
}

# Official Rare and Legendary node names and +10 Core Stat grants per board from DiabloTools/d4data:
#   board_id -> {(unrot_row, unrot_col): (in_game_name, core_stat_or_None)}
BOARD_SPECIAL_NODES: dict[str, dict[tuple[int, int], tuple[str, str | None]]] = {
    "Paragon_Barb_00": {(4, 9): ("Raw Power", "STR"), (4, 11): ("Iron Strength", "STR"), (11, 8): ("Tenacity", None), (11, 12): ("Brawn", None)},
    "Paragon_Barb_01": {(2, 15): ("Butcher", "STR"), (5, 18): ("Flayer", "STR"), (7, 5): ("Blood Clotting", None), (9, 11): ("Bloodbathed", None), (15, 5): ("Hemorrhage", None), (15, 13): ("Fleeting", None), (18, 2): ("Lacerator", None)},
    "Paragon_Barb_02": {(3, 14): ("Restorative", None), (5, 5): ("Grit", None), (9, 9): ("Blood Rage", None), (9, 12): ("Wrath", "STR"), (12, 9): ("Revel", "STR"), (14, 3): ("Bloodless", None), (17, 17): ("Craving", None)},
    "Paragon_Barb_03": {(3, 15): ("Tenacity", None), (6, 4): ("Brash", None), (8, 10): ("Fierce", "STR"), (10, 6): ("Carnage", None), (12, 10): ("Berserker", "STR"), (14, 4): ("Recuperate", None), (17, 15): ("Enraged", None)},
    "Paragon_Barb_04": {(8, 7): ("Demolish", "STR"), (8, 13): ("Destroyer", "STR"), (14, 3): ("Brawn", None), (14, 17): ("Pillage", None), (15, 10): ("Decimator", None), (16, 6): ("Arrogance", None), (16, 14): ("Restorative", None)},
    "Paragon_Barb_05": {(3, 17): ("Bone Breaker", None), (5, 15): ("Bulwark", None), (6, 7): ("Tenacity", None), (10, 10): ("Inner Strength", None), (13, 14): ("Vigor", None), (15, 2): ("Brutality", "STR"), (18, 5): ("Bludgeoner", "STR")},
    "Paragon_Barb_06": {(3, 5): ("Defensive Stance", None), (3, 15): ("Brash", None), (6, 10): ("Flawless Technique", None), (7, 5): ("Nimble", None), (7, 15): ("Slayer", None), (12, 7): ("Heavy Blows", "STR"), (12, 13): ("Wild Force", "STR")},
    "Paragon_Barb_07": {(6, 12): ("Fire Resilience", None), (7, 6): ("Hungering Fury", None), (8, 14): ("Conditioned", None), (12, 8): ("Warbringer", None), (14, 13): ("Core Reserve", None), (15, 2): ("Raw Power", "STR"), (18, 5): ("Brute Force", "STR")},
    "Paragon_Barb_08": {(3, 7): ("Raw Power", "STR"), (7, 3): ("Iron Strength", "STR"), (7, 11): ("Hunter Killer", None), (11, 7): ("Nimble", None), (11, 15): ("Denial", None), (15, 11): ("Deflection", None), (17, 17): ("Weapons Master", None)},
    "Paragon_Barb_10": {(5, 7): ("Squall", "STR"), (5, 13): ("Raw Might", "STR"), (10, 4): ("Alloyed", None), (10, 16): ("Denial", None), (13, 10): ("Force of Nature", None), (15, 7): ("Tenacity", None), (15, 13): ("Catalyst", None)},
    "Paragon_Druid_00": {(4, 9): ("Impel", "WILL"), (4, 11): ("Reclaim", "WILL"), (11, 8): ("Tenacity", None), (11, 12): ("Prime", None)},
    "Paragon_Druid_01": {(2, 15): ("Stormcaller", "WILL"), (5, 18): ("Concentrated", "WILL"), (7, 5): ("Insulated", None), (9, 11): ("Deluge", None), (15, 5): ("Thunderstruck", None), (15, 13): ("Hubris", None), (18, 2): ("Tempest", None)},
    "Paragon_Druid_02": {(3, 14): ("Oppress", None), (5, 5): ("Suffused Resilience", None), (9, 9): ("Earthen Devastation", None), (9, 12): ("Crushing Earth", "WILL"), (12, 9): ("Earthen Power", "WILL"), (14, 3): ("Slayer", None), (17, 17): ("Resolve", None)},
    "Paragon_Druid_03": {(3, 15): ("Zealous", None), (6, 4): ("Spearhead", None), (8, 10): ("Grizzly", "WILL"), (10, 6): ("Survival Instincts", None), (12, 10): ("Battleworn Hide", "WILL"), (14, 4): ("Bulwark", None), (17, 15): ("Ursine", None)},
    "Paragon_Druid_04": {(8, 7): ("Feral", "WILL"), (8, 13): ("Ripper", "WILL"), (14, 3): ("Recuperate", None), (14, 17): ("Wild's Call", None), (15, 10): ("Lust for Carnage", None), (16, 6): ("Ferocity", None), (16, 14): ("Fleeting", None)},
    "Paragon_Druid_05": {(3, 17): ("Heightened Malice", None), (5, 15): ("Immunization", None), (6, 7): ("Denial", None), (10, 10): ("Nature-born", None), (13, 14): ("Restorative", None), (15, 2): ("Sinking Fangs", "WILL"), (18, 5): ("Impel", "WILL")},
    "Paragon_Druid_06": {(3, 5): ("Slayer", None), (3, 15): ("Wilds", None), (6, 10): ("Inner Beast", None), (7, 5): ("Nimble", None), (7, 15): ("Tenacity", None), (12, 7): ("Shapeshifter", "WILL"), (12, 13): ("Impel", "WILL")},
    "Paragon_Druid_07": {(6, 12): ("Denial", None), (7, 6): ("Suffused Resilience", None), (8, 14): ("Superiority", None), (12, 8): ("Constricting Tendrils", None), (14, 13): ("Courage", None), (15, 2): ("Nature's Will", "WILL"), (18, 5): ("Devastation", "WILL")},
    "Paragon_Druid_08": {(3, 7): ("Harmony", "WILL"), (7, 3): ("Guidance", "WILL"), (7, 11): ("Natural Attunement", None), (11, 7): ("Spiritual Power", None), (11, 15): ("Reclamation", None), (15, 11): ("Resolve", None), (17, 17): ("Ancestral Guidance", None)},
    "Paragon_Druid_10": {(5, 7): ("Apex", "WILL"), (5, 13): ("Fortune", "WILL"), (10, 4): ("Ferocity", None), (10, 16): ("Eminence", None), (13, 10): ("Untamed", None), (15, 7): ("Lucky Winds", None), (15, 13): ("Resolve", None)},
    "Paragon_Necro_00": {(4, 9): ("Knowledge", "INT"), (4, 11): ("Grasp", "INT"), (11, 8): ("Resilience", None), (11, 12): ("Prime", None)},
    "Paragon_Necro_01": {(2, 15): ("Overlord", "INT"), (5, 18): ("Custody", "INT"), (7, 5): ("Infused Caster", None), (9, 11): ("Puppeteer", None), (15, 5): ("Cult Leader", None), (15, 13): ("Infused Warrior", None), (18, 2): ("Erudite", None)},
    "Paragon_Necro_02": {(3, 14): ("Infused Golem", None), (5, 5): ("Flesh-horror", None), (9, 9): ("Hulking Monstrosity", None), (9, 12): ("Miscreation", "INT"), (12, 9): ("Mutation", "INT"), (14, 3): ("Borrowed Strength", None), (17, 17): ("Frenzied Golem", None)},
    "Paragon_Necro_03": {(3, 15): ("Blood Clotting", None), (6, 4): ("Stifle", None), (8, 10): ("Targeted", "INT"), (10, 6): ("Flesh-eater", None), (12, 10): ("Rend", "INT"), (14, 4): ("Recuperate", None), (17, 15): ("Erudite", None)},
    "Paragon_Necro_04": {(8, 7): ("Deathmarked", "INT"), (8, 13): ("Seethe", "INT"), (14, 3): ("Deathbringer", None), (14, 17): ("Restorative", None), (15, 10): ("Scent of Death", None), (16, 6): ("Suffused Resilience", None), (16, 14): ("Shrouded", None)},
    "Paragon_Necro_05": {(3, 17): ("Bone Graft", None), (5, 15): ("Entomb", None), (6, 7): ("Erudite", None), (10, 10): ("Suffused Resilience", None), (13, 14): ("Reinvigorate", None), (15, 2): ("Shaper of Bone", "INT"), (18, 5): ("Splinter", "INT")},
    "Paragon_Necro_06": {(3, 5): ("Recuperate", None), (3, 15): ("Blooddrinker", None), (6, 10): ("Blood Begets Blood", None), (7, 5): ("Vampiric", None), (7, 15): ("Invigorated", None), (12, 7): ("Blood-Empowered", "INT"), (12, 13): ("Aggression", "INT")},
    "Paragon_Necro_07": {(6, 12): ("Guarded Advance", None), (7, 6): ("Powerhouse", None), (8, 14): ("Suffused Resilience", None), (12, 8): ("Bloodbath", None), (14, 13): ("Thick Hide", None), (15, 2): ("Hardened", "INT"), (18, 5): ("Remedy", "INT")},
    "Paragon_Necro_08": {(3, 7): ("Malediction", "INT"), (7, 3): ("Gnawing Darkness", "INT"), (7, 11): ("Lingering Shadows", None), (11, 7): ("Gloom", None), (11, 15): ("Dragging Shadows", None), (15, 11): ("Restorative", None), (17, 17): ("Wither", None)},
    "Paragon_Necro_10": {(5, 7): ("Eradicate", "INT"), (5, 13): ("Preservation", "INT"), (10, 4): ("Relentless", None), (10, 16): ("Lingering Shadows", None), (13, 10): ("Frailty", None), (15, 7): ("Meticulous", None), (15, 13): ("Shrouded", None)},
    "Paragon_Paladin_00": {(4, 9): ("Raw Power", "STR"), (4, 11): ("Iron Strength", "STR"), (11, 8): ("Tenacity", None), (11, 12): ("Brawn", None)},
    "Paragon_Paladin_01": {(2, 15): ("Knight", "STR"), (5, 18): ("Ironclad", "STR"), (7, 5): ("Insulate", None), (9, 11): ("Stalwart", None), (15, 5): ("Castle", None), (15, 13): ("Grappler", None), (18, 2): ("Defiance", None)},
    "Paragon_Paladin_02": {(3, 14): ("Stalwart", None), (5, 5): ("Bodyguard", None), (9, 9): ("Shield Bearer", None), (9, 12): ("Knight", "STR"), (12, 9): ("Constrict", "STR"), (14, 3): ("Bludgeoning", None), (17, 17): ("Growth", None)},
    "Paragon_Paladin_03": {(3, 15): ("Constitution", None), (6, 4): ("Sturdy", None), (8, 10): ("Knight", "STR"), (10, 6): ("Fervent", None), (12, 10): ("Fencing", "STR"), (14, 4): ("Surgical", None), (17, 15): ("Skirmish", None)},
    "Paragon_Paladin_04": {(8, 7): ("Hearty", "STR"), (8, 13): ("Whale", "STR"), (14, 3): ("Recovery", None), (14, 17): ("Bolster", None), (15, 10): ("Preacher", None), (16, 6): ("Inner Strength", None), (16, 14): ("Friction", None)},
    "Paragon_Paladin_05": {(3, 17): ("Divinity", None), (5, 15): ("Sturdy", None), (6, 7): ("Defiance", None), (10, 10): ("Oppressive", None), (13, 14): ("Cautious Fighter", None), (15, 2): ("Flayer", "STR"), (18, 5): ("Champion", "STR")},
    "Paragon_Paladin_06": {(3, 5): ("Battering Ram", None), (3, 15): ("Cull", None), (6, 10): ("Relentless", None), (7, 5): ("Inured", None), (7, 15): ("Defiance", None), (12, 7): ("Endure", "STR"), (12, 13): ("Dervish", "STR")},
    "Paragon_Paladin_07": {(6, 12): ("Benediction", None), (7, 6): ("Restorative", None), (8, 14): ("Sturdy", None), (12, 8): ("Sentencing", None), (14, 13): ("Luminous", None), (15, 2): ("Grace", "STR"), (18, 5): ("Endure", "STR")},
    "Paragon_Paladin_08": {(3, 7): ("Grace", "STR"), (7, 3): ("Wrathful", "STR"), (7, 11): ("Restorative", None), (11, 7): ("Anointed", None), (11, 15): ("Radiance", None), (15, 11): ("Restorative", None), (17, 17): ("Endure", None)},
    "Paragon_Paladin_09": {(5, 7): ("Champion", "STR"), (5, 13): ("Fortitude", "STR"), (10, 4): ("Constitution", None), (10, 16): ("Blurring", None), (13, 10): ("Beacon", None), (15, 7): ("Spiked Shield", None), (15, 13): ("Sturdy", None)},
    "Paragon_Rogue_00": {(4, 9): ("Skillful", "DEX"), (4, 11): ("Outlaw", "DEX"), (11, 8): ("Resilience", None), (11, 12): ("Prime", None)},
    "Paragon_Rogue_01": {(2, 15): ("Potent", "DEX"), (5, 18): ("Concoction", "DEX"), (7, 5): ("Suffused Resilience", None), (9, 11): ("Ready Supply", None), (15, 5): ("Eldritch Bounty", None), (15, 13): ("Restorative", None), (18, 2): ("Imbuer", None)},
    "Paragon_Rogue_02": {(3, 14): ("Brawler", None), (5, 5): ("Shrouded", None), (9, 9): ("Tricks of the Trade", None), (9, 12): ("Surgical", "DEX"), (12, 9): ("Focused", "DEX"), (14, 3): ("Ranger", None), (17, 17): ("Haven", None)},
    "Paragon_Rogue_03": {(3, 15): ("Calculated", None), (6, 4): ("Deviant", None), (8, 10): ("Devious", "DEX"), (10, 6): ("Cheap Shot", None), (12, 10): ("Artifice", "DEX"), (14, 4): ("Safeguard", None), (17, 15): ("Spearhead", None)},
    "Paragon_Rogue_04": {(8, 7): ("Engineering", "DEX"), (8, 13): ("Ensnarement", "DEX"), (14, 3): ("Immunization", None), (14, 17): ("Spring-loaded", None), (15, 10): ("Deadly Ambush", None), (16, 6): ("Trapper", None), (16, 14): ("Cunning", None)},
    "Paragon_Rogue_05": {(3, 17): ("Leyrana's Instinct", None), (5, 15): ("Suffused Resilience", None), (6, 7): ("Immunization", None), (10, 10): ("Nimble", None), (13, 14): ("Restorative", None), (15, 2): ("Essential", "DEX"), (18, 5): ("Punishment", "DEX")},
    "Paragon_Rogue_06": {(3, 5): ("Recuperate", None), (3, 15): ("Culler", None), (6, 10): ("No Witnesses", None), (7, 5): ("Training", None), (7, 15): ("Fleeting", None), (12, 7): ("Skillful", "DEX"), (12, 13): ("Deliverance", "DEX")},
    "Paragon_Rogue_07": {(6, 12): ("Defensive Stance", None), (7, 6): ("Toughened", None), (8, 14): ("Unassailable", None), (12, 8): ("Exploit Weakness", None), (14, 13): ("Exploit", None), (15, 2): ("Artifice", "DEX"), (18, 5): ("Misery", "DEX")},
    "Paragon_Rogue_08": {(3, 7): ("Finisher", "DEX"), (7, 3): ("Fundamentals", "DEX"), (7, 11): ("Dominant", None), (11, 7): ("Haven", None), (11, 15): ("Denial", None), (15, 11): ("Slayer", None), (17, 17): ("Cunning Stratagem", None)},
    "Paragon_Rogue_10": {(5, 7): ("Giant Killer", "DEX"), (5, 13): ("Assassin", "DEX"), (10, 4): ("Ranger", None), (10, 16): ("Brawler", None), (13, 10): ("Danse Macabre", None), (15, 7): ("Acrobat", None), (15, 13): ("Skulker", None)},
    "Paragon_Sorc_00": {(4, 9): ("Elemental Balance", "INT"), (4, 11): ("Studied", "INT"), (11, 8): ("Resilience", None), (11, 12): ("Elementalist", None)},
    "Paragon_Sorc_01": {(2, 15): ("Flame-touched", "INT"), (5, 18): ("Explosive", "INT"), (7, 5): ("Keeper of Flames", None), (9, 11): ("Combustion", None), (15, 5): ("Searing Heat", None), (15, 13): ("Recuperate", None), (18, 2): ("Ashes", None)},
    "Paragon_Sorc_02": {(3, 14): ("Restorative", None), (5, 5): ("Advantage", None), (9, 9): ("Frigid Fate", None), (9, 12): ("Elemental Favor", "INT"), (12, 9): ("Weakness", "INT"), (14, 3): ("Guarded", None), (17, 17): ("Oppressive", None)},
    "Paragon_Sorc_03": {(3, 15): ("Insulated", None), (6, 4): ("Blessing", None), (8, 10): ("Incapacitate", "INT"), (10, 6): ("Static Surge", None), (12, 10): ("Electro", "INT"), (14, 4): ("Restorative", None), (17, 15): ("Paralyzing", None)},
    "Paragon_Sorc_04": {(8, 7): ("Conjurer", "INT"), (8, 13): ("Keeper of Elements", "INT"), (14, 3): ("Reservoir", None), (14, 17): ("Resistant", None), (15, 10): ("Elemental Summoner", None), (16, 6): ("Swift Conjurer", None), (16, 14): ("Fleeting", None)},
    "Paragon_Sorc_05": {(3, 17): ("Burning Instinct", None), (5, 15): ("Keeper of Flames", None), (6, 7): ("Safeguard", None), (10, 10): ("Kindling", None), (13, 14): ("Smoldering Embers", None), (15, 2): ("Cinders", "INT"), (18, 5): ("Explosive", "INT")},
    "Paragon_Sorc_06": {(3, 5): ("Keeper of Winter", None), (3, 15): ("Frigid", None), (6, 10): ("Icefall", None), (7, 5): ("Polar Rime", None), (7, 15): ("Recuperate", None), (12, 7): ("Frosts", "INT"), (12, 13): ("Cryomancy", "INT")},
    "Paragon_Sorc_07": {(6, 12): ("Suffused Resilience", None), (7, 6): ("Conduit", None), (8, 14): ("Insulated", None), (12, 8): ("Ceaseless Conduit", None), (14, 13): ("Shelter", None), (15, 2): ("Galvanic Catalyst", "INT"), (18, 5): ("Devastate", "INT")},
    "Paragon_Sorc_08": {(3, 7): ("Elemental Balance", "INT"), (7, 3): ("Tactical", "INT"), (7, 11): ("Toughened", None), (11, 7): ("Ruinous", None), (11, 15): ("Suffused Resilience", None), (15, 11): ("Blessing", None), (17, 17): ("Enchantment Master", None)},
    "Paragon_Sorc_10": {(5, 7): ("Lightning Cutter", "INT"), (5, 13): ("Elemental Balance", "INT"), (10, 4): ("Overdrive", None), (10, 16): ("Electromagnetism", None), (13, 10): ("Fundamental Release", None), (15, 7): ("Kindling", None), (15, 13): ("Suffused Resilience", None)},
    "Paragon_Spirit_0": {(4, 9): ("Skillful", "DEX"), (4, 11): ("Spiritual", "DEX"), (11, 8): ("Tenacity", None), (11, 12): ("Prime", None)},
    "Paragon_Spirit_00": {(4, 9): ("Skillful", "DEX"), (4, 11): ("Spiritual", "DEX"), (11, 8): ("Tenacity", None), (11, 12): ("Prime", None)},
    "Paragon_Spirit_01": {(2, 15): ("Rampage", "DEX"), (5, 18): ("Master", "DEX"), (7, 5): ("Training", None), (9, 11): ("Primal Rage", None), (15, 5): ("In-Fighter", None), (15, 13): ("Mosquito", None), (18, 2): ("Incombustible", None)},
    "Paragon_Spirit_02": {(3, 14): ("Vigilant", None), (5, 5): ("Decay", None), (9, 9): ("Spiney Skin", None), (9, 12): ("Bristles", "DEX"), (12, 9): ("Prophetic", "DEX"), (14, 3): ("Defiance", None), (17, 17): ("Stoic", None)},
    "Paragon_Spirit_03": {(3, 15): ("Primal Ward", None), (6, 4): ("Slayer", None), (8, 10): ("Raptor", "DEX"), (10, 6): ("Viscous Shield", None), (12, 10): ("Fearless", "DEX"), (14, 4): ("Bristle", None), (17, 15): ("Sentinel", None)},
    "Paragon_Spirit_04": {(8, 7): ("Gifted", "DEX"), (8, 13): ("Ambush", "DEX"), (14, 3): ("Immunization", None), (14, 17): ("Restorative", None), (15, 10): ("Bitter Medicine", None), (16, 6): ("Natural Vitality", None), (16, 14): ("Flash Fire", None)},
    "Paragon_Spirit_05": {(3, 17): ("Revealing", None), (5, 15): ("Haven", None), (6, 7): ("Patience", None), (10, 10): ("Grounded", None), (13, 14): ("Sentinel", None), (15, 2): ("Artifice", "DEX"), (18, 5): ("Venom Weaver", "DEX")},
    "Paragon_Spirit_06": {(3, 5): ("Restorative", None), (3, 15): ("Nature's Dance", None), (6, 10): ("Drive", None), (7, 5): ("Piercing Gale", None), (7, 15): ("Zephyr", None), (12, 7): ("Tolerance", "DEX"), (12, 13): ("Peerless", "DEX")},
    "Paragon_Spirit_07": {(6, 12): ("Elementalist", None), (7, 6): ("Training", None), (8, 14): ("Ruinous", None), (12, 8): ("Convergence", None), (14, 13): ("Denial", None), (15, 2): ("Spearhead", "DEX"), (18, 5): ("Skillful", "DEX")},
    "Paragon_Spirit_08": {(3, 7): ("Concentrated", "DEX"), (7, 3): ("Eager Prey", "DEX"), (7, 11): ("Sprightful", None), (11, 7): ("Denial", None), (11, 15): ("Resilience", None), (15, 11): ("Opportunist", None), (17, 17): ("Prodigy's Tempo", None)},
    "Paragon_Spirit_10": {(5, 7): ("Arthropodal", "DEX"), (5, 13): ("Avian", "DEX"), (10, 4): ("Flash Fire", None), (10, 16): ("Haven", None), (13, 10): ("Swarm of Storms", None), (15, 7): ("Training", None), (15, 13): ("Sentinel", None)},
    "Paragon_Warlock_00": {(4, 9): ("Warped", "WILL"), (4, 11): ("Domination", "WILL"), (11, 8): ("Resilience", None), (11, 12): ("Prime", None)},
    "Paragon_Warlock_01": {(2, 15): ("Pact Sign", "WILL"), (5, 18): ("Chaining", "WILL"), (7, 5): ("Recovery Seal", None), (9, 11): ("Shielding Sigil", None), (15, 5): ("Fathomless", None), (15, 13): ("Binding", None), (18, 2): ("Flesh Guard", None)},
    "Paragon_Warlock_02": {(3, 14): ("Hellish Recovery", None), (5, 5): ("Demon Wall", None), (9, 9): ("Demonic Spicules", None), (9, 12): ("Advantage", "WILL"), (12, 9): ("Spined", "WILL"), (14, 3): ("Blood Ward", None), (17, 17): ("Void Amplifier", None)},
    "Paragon_Warlock_03": {(3, 15): ("Fiendish Renewal", None), (6, 4): ("Tortured", None), (8, 10): ("Bolster", "WILL"), (10, 6): ("Dynamism", None), (12, 10): ("Augmented", "WILL"), (14, 4): ("Demonskin", None), (17, 15): ("Eldrich Carapace", None)},
    "Paragon_Warlock_04": {(8, 7): ("Burn", "WILL"), (8, 13): ("Blaze", "WILL"), (14, 3): ("Flames Devour", None), (14, 17): ("Charred Flesh", None), (15, 10): ("Pyrosis", None), (16, 6): ("Infernal Ward", None), (16, 14): ("Heat Shield", None)},
    "Paragon_Warlock_05": {(3, 17): ("Overmind", None), (5, 15): ("Coercion", None), (6, 7): ("Manipulation", None), (10, 10): ("Mental Barrier", None), (13, 14): ("Influence", None), (15, 2): ("Cycle", "WILL"), (18, 5): ("Voodoo", "WILL")},
    "Paragon_Warlock_06": {(3, 5): ("Resist", None), (3, 15): ("Death Mark", None), (6, 10): ("Greater Hex", None), (7, 5): ("Hexguard", None), (7, 15): ("Mending Ward", None), (12, 7): ("Perilous Depths", "WILL"), (12, 13): ("Forbidden Contact", "WILL")},
    "Paragon_Warlock_07": {(6, 12): ("Descent", None), (7, 6): ("Dark Sign", None), (8, 14): ("Patient Violence", None), (12, 8): ("Ritualism", None), (14, 13): ("Gethsemane", None), (15, 2): ("Emergence", "WILL"), (18, 5): ("Vicious Cycle", "WILL")},
    "Paragon_Warlock_08": {(3, 7): ("Overload", "WILL"), (7, 3): ("Fractal Beam", "WILL"), (7, 11): ("Control", None), (11, 7): ("Mutation", None), (11, 15): ("Distortion Wave", None), (15, 11): ("Entropy Collapse", None), (17, 17): ("Chaos", None)},
    "Paragon_Warlock_10": {(5, 7): ("Rune Charged", "WILL"), (5, 13): ("Jagged Claws", "WILL"), (10, 4): ("Auxiliary Flesh", None), (10, 16): ("Mortal Lock", None), (13, 10): ("Dominion", None), (15, 7): ("Armored Summons", None), (15, 13): ("Eye of Newt", None)},
}

# Exact Core Stat values granted by Paragon nodes in Diablo 4 (from AttributeFormulas.gam.json):
#   - Normal stat nodes (DEX, INT, STR, WILL): +5
#   - Magic stat nodes (MAGIC_DEX, MAGIC_INT, MAGIC_STR, MAGIC_WILL): +7 (1.4x normal)
#   - Rare nodes with ParagonNodeCoreStat_RareMinor/Major: +10 (2.0x normal)
#   - Gate (EXIT) nodes: +5 to all 4 core stats
NORMAL_STAT_GRANT = 5
MAGIC_STAT_GRANT = 7
RARE_STAT_GRANT = 10


def get_glyph_radius(glyph_name: str, glyph_level: int) -> int:
    """
    Diablo 4 Glyph radius in Manhattan (L1) grid units around the GLYPH socket:
      - Level 1..14:  radius 3
      - Level 15..45: radius 4
      - Level 46+:    radius 5
    """
    if not glyph_name:
        return 0
    if glyph_level >= 46:
        return 5
    if glyph_level >= 15:
        return 4
    return 3


def get_glyph_stat_types(glyph_name: str) -> tuple[NODE_TYPE, ...]:
    """Return the stat node types governed by a Diablo 4 Glyph (using d4data GLYPH_DATA with name fallback)."""
    if not glyph_name:
        return ()
    if glyph_name in GLYPH_DATA:
        stat = GLYPH_DATA[glyph_name][1]
        if stat in STAT_ALIASES:
            return STAT_ALIASES[stat]
    low = glyph_name.lower()
    if "dexterity" in low or "_dex_" in low or low.endswith("_dex"):
        return (NODE_TYPE.DEX, NODE_TYPE.MAGIC_DEX)
    if "intelligence" in low or "_int_" in low or low.endswith("_int"):
        return (NODE_TYPE.INT, NODE_TYPE.MAGIC_INT)
    if "strength" in low or "_str_" in low or low.endswith("_str"):
        return (NODE_TYPE.STR, NODE_TYPE.MAGIC_STR)
    if "willpower" in low or "_will_" in low or low.endswith("_will"):
        return (NODE_TYPE.WILL, NODE_TYPE.MAGIC_WILL)
    return ()
