"""
Battle Engine — Path of Titans Primordial Tyrants combat simulation.

Authentic PoT formula: Damage = BaseDamage × (AttackerCW / DefenderCW)
Features: individual pack member HP, flee mechanic, hit zones, crits,
dodge, species abilities, status effects, pack bonuses, damage variance.
"""

import random
import math

# ── Species Family Mapping ──────────────────────────────────────
SPECIES_FAMILIES = {
    "tyrannosaurus": "tyrannosaurid", "giganotosaurus": "tyrannosaurid",
    "alioramus": "tyrannosaurid", "daspletosaurus": "tyrannosaurid",
    "tarbosaurus": "tyrannosaurid", "gorgosaurus": "tyrannosaurid",
    "albertosaurus": "tyrannosaurid", "yutyrannus": "tyrannosaurid",

    "utahraptor": "raptor", "achillobator": "raptor",
    "deinonychus": "raptor", "latenivenatrix": "raptor",
    "concavenator": "raptor",

    "triceratops": "ceratopsian", "albertaceratops": "ceratopsian",
    "styracosaurus": "ceratopsian", "pachyrhinosaurus": "ceratopsian",
    "ceratosaurus": "ceratopsian", "diabloceratops": "ceratopsian",
    "einiosaurus": "ceratopsian", "kosmoceratops": "ceratopsian",
    "medusaceratops": "ceratopsian", "nasutoceratops": "ceratopsian",
    "regaliceratops": "ceratopsian", "sinoceratops": "ceratopsian",
    "torosaurus": "ceratopsian", "zuniceratops": "ceratopsian",

    "lambeosaurus": "hadrosaur", "parasaurolophus": "hadrosaur",
    "iguanodon": "hadrosaur", "barsboldia": "hadrosaur",
    "camptosaurus": "hadrosaur", "corythosaurus": "hadrosaur",
    "edmontosaurus": "hadrosaur", "maiasaura": "hadrosaur",
    "olorotitan": "hadrosaur", "saurolophus": "hadrosaur",

    "anodontosaurus": "ankylosaur", "ankylosaurus": "ankylosaur",
    "kentrosaurus": "ankylosaur", "stegosaurus": "ankylosaur",

    "amargasaurus": "sauropod", "deinocheirus": "sauropod",
    "therizinosaurus": "therizinosaur",
}

# ── Group Slot Calc ─────────────────────────────────────────────
def get_group_slots(cw):
    if cw >= 7000: return 5
    if cw >= 5000: return 4
    if cw >= 3000: return 3
    if cw >= 1500: return 2
    return 1

# ── Hit Zones ───────────────────────────────────────────────────
HIT_ZONES = [("HEAD", 0.20, 1.20), ("BODY", 0.55, 1.00),
             ("TAIL", 0.15, 0.25), ("FLANK", 0.10, 0.80)]

def roll_hit_zone():
    r = random.random()
    c = 0
    for name, prob, mult in HIT_ZONES:
        c += prob
        if r <= c:
            return name, mult
    return "BODY", 1.0

# ── Ability Pools ───────────────────────────────────────────────
def get_ability_pool(family, dtype, base_atk):
    pools = {
        "tyrannosaurid": [
            {"name": "Bite",          "base": base_atk,           "cd": 0, "effects": [],
             "desc": "a powerful jaw clamp"},
            {"name": "Charged Bite",  "base": int(base_atk*2.2),  "cd": 3, "effects": [],
             "desc": "a devastating charged jaw crush"},
            {"name": "Crushing Bite", "base": int(base_atk*1.5),  "cd": 2,
             "effects": [{"type": "bonebreak", "dur": 2}],
             "desc": "a bone-shattering crunch"},
            {"name": "Tyrant Stomp",  "base": int(base_atk*0.8),  "cd": 1, "effects": [],
             "desc": "a thundering stomp"},
        ],
        "raptor": [
            {"name": "Raptor Strikes","base": int(base_atk*0.7),  "cd": 0, "effects": [],
             "desc": "a flurry of rapid claw swipes"},
            {"name": "Pounce",        "base": int(base_atk*1.8),  "cd": 2,
             "effects": [{"type": "bleed", "dur": 3, "pct": 0.03}],
             "desc": "a leaping pounce attack"},
            {"name": "Claw Slash",    "base": int(base_atk*1.2),  "cd": 1, "effects": [],
             "desc": "a vicious claw rake"},
            {"name": "Ripping Kick",  "base": int(base_atk*0.5),  "cd": 1,
             "effects": [{"type": "bleed", "dur": 2, "pct": 0.02}],
             "desc": "a slashing kick that tears flesh"},
        ],
        "therizinosaur": [
            {"name": "Scythe Swipe",  "base": int(base_atk*1.2),  "cd": 0, "effects": [],
             "desc": "a sweeping scythe claw slash"},
            {"name": "Rending Slash", "base": int(base_atk*2.0),  "cd": 2,
             "effects": [{"type": "bleed", "dur": 3, "pct": 0.04}],
             "desc": "a deep rending claw tear"},
            {"name": "Defensive Stance","base": 0,                "cd": 3,
             "effects": [{"type": "defense", "dur": 2, "reduction": 0.5}],
             "desc": "raises claws to block incoming"},
        ],
        "ceratopsian": [
            {"name": "Horn Thrust",   "base": base_atk,           "cd": 0, "effects": [],
             "desc": "a forward horn jab"},
            {"name": "Charge",        "base": int(base_atk*2.0),  "cd": 3,
             "effects": [{"type": "bonebreak", "dur": 2}],
             "desc": "a full-speed horn charge"},
            {"name": "Headbutt",      "base": int(base_atk*1.3),  "cd": 1, "effects": [],
             "desc": "a heavy frill-first headbutt"},
        ],
        "ankylosaur": [
            {"name": "Tail Club",     "base": int(base_atk*1.1),  "cd": 0, "effects": [],
             "desc": "a heavy tail club swing"},
            {"name": "Tail Slam",     "base": int(base_atk*1.8),  "cd": 2,
             "effects": [{"type": "bonebreak", "dur": 2}],
             "desc": "a crushing tail slam"},
            {"name": "Spike Guard",   "base": int(base_atk*0.4),  "cd": 2,
             "effects": [{"type": "defense", "dur": 1, "reduction": 0.6}],
             "desc": "hunkers behind armored plates"},
        ],
        "hadrosaur": [
            {"name": "Kick",          "base": int(base_atk*0.9),  "cd": 0, "effects": [],
             "desc": "a swift rear kick"},
            {"name": "Tail Sweep",    "base": int(base_atk*1.4),  "cd": 2, "effects": [],
             "desc": "a wide sweeping tail strike"},
            {"name": "Alarm Call",    "base": 0,                  "cd": 3,
             "effects": [{"type": "heal", "pct": 0.08}],
             "desc": "a booming rally call"},
        ],
        "sauropod": [
            {"name": "Stomp",         "base": base_atk,           "cd": 0, "effects": [],
             "desc": "a ground-shaking stomp"},
            {"name": "Tail Whip",     "base": int(base_atk*1.6),  "cd": 2, "effects": [],
             "desc": "a massive whipping tail strike"},
            {"name": "Tremor",        "base": int(base_atk*0.6),  "cd": 3,
             "effects": [{"type": "bonebreak", "dur": 1}],
             "desc": "shakes the earth, rattling bones"},
        ],
    }
    if family in pools:
        return pools[family]
    if dtype == "carnivore":
        return [
            {"name": "Bite", "base": base_atk, "cd": 0, "effects": [], "desc": "a snapping bite"},
            {"name": "Lunge", "base": int(base_atk*1.6), "cd": 2,
             "effects": [{"type": "bleed", "dur": 2, "pct": 0.02}], "desc": "a lunging snap"},
        ]
    return [
        {"name": "Kick", "base": int(base_atk*0.9), "cd": 0, "effects": [], "desc": "a rear-leg kick"},
        {"name": "Tail Sweep", "base": int(base_atk*1.5), "cd": 2, "effects": [], "desc": "a sweeping tail"},
    ]


# ── Passives ────────────────────────────────────────────────────
PASSIVES = {
    "tyrannosaurid": ("Tyrant Roar",  "+10% ATK per Tyrannosaurid ally"),
    "raptor":        ("Pack Bark",    "+8% ATK per Raptor ally"),
    "ceratopsian":   ("Herd Shield",  "+5% Armor per Ceratopsian ally"),
    "ankylosaur":    ("Shell Wall",   "+8% Armor per Ankylosaur ally"),
    "therizinosaur": ("Lone Survivor","+10% Armor when solo"),
}


# ── Individual Pack Member ──────────────────────────────────────
class PackMember:
    """One individual dino within a pack/team. Tracks its own HP and status."""

    def __init__(self, dino_data, index=0):
        self.data = dino_data
        self.dino_id = dino_data.get("id", "unknown")
        self.base_name = dino_data["name"]
        self.index = index
        self.family = SPECIES_FAMILIES.get(self.dino_id.lower(), "generic")
        self.dtype = dino_data.get("type", "carnivore")

        # Individual stats
        self.cw = dino_data.get("cw", 3000)
        self.max_hp = dino_data.get("hp", 500)
        self.hp = self.max_hp
        self.base_atk = dino_data.get("atk", 50)
        self.armor = dino_data.get("armor", 1.0)
        self.spd = dino_data.get("spd", 500)

        # Combat state
        self.atk_bonus = 0.0
        self.armor_bonus = 0.0
        self.status_effects = []
        self.defense_up = 0.0
        self.fled = False

        # Abilities and cooldowns (each member has their own)
        # Use custom abilities if defined, otherwise use family pool
        if dino_data.get('custom_abilities'):
            # Scale custom ability base values relative to this dino's actual ATK
            self.abilities = []
            for ab in dino_data['custom_abilities']:
                scaled = dict(ab)
                # 'base' in custom abilities is stored as multiplier*100 (100 = 1.0x ATK)
                scaled['base'] = int(self.base_atk * (ab.get('base', 100) / 100.0))
                self.abilities.append(scaled)
        else:
            self.abilities = get_ability_pool(self.family, self.dtype, self.base_atk)
        self.cooldowns = {a["name"]: 0 for a in self.abilities}

    @property
    def label(self):
        if self.index > 0:
            return f"{self.base_name} #{self.index + 1}"
        return self.base_name

    @property
    def alive(self):
        return self.hp > 0 and not self.fled

    def pick_ability(self):
        available = [a for a in self.abilities if self.cooldowns.get(a["name"], 0) <= 0]
        if not available:
            available = [a for a in self.abilities if a["cd"] == 0]
        if not available:
            return {"name": "Struggle", "base": max(5, int(self.base_atk * 0.3)),
                    "cd": 0, "effects": [], "desc": "a desperate flailing attack"}
        weights = []
        for a in available:
            w = 30 if a["cd"] > 0 else 10
            if self.hp < self.max_hp * 0.3:
                for eff in a.get("effects", []):
                    if eff["type"] in ("defense", "heal"):
                        w += 40
            weights.append(w)
        total = sum(weights)
        r = random.random() * total
        cumul = 0
        for i, w in enumerate(weights):
            cumul += w
            if r <= cumul:
                return available[i]
        return available[0]

    def tick_cooldowns(self):
        for name in self.cooldowns:
            if self.cooldowns[name] > 0:
                self.cooldowns[name] -= 1

    def tick_status_effects(self):
        logs = []
        self.defense_up = 0.0
        new = []
        for eff in self.status_effects:
            if eff["type"] == "bleed":
                dmg = max(1, int(self.max_hp * eff.get("pct", 0.03)))
                self.hp = max(0, self.hp - dmg)
                logs.append(f"  🩸 {self.label} bleeds for **{dmg}** ({eff['remaining']} turns)")
            elif eff["type"] == "bonebreak":
                logs.append(f"  🦴 {self.label} — bonebroken, dodge disabled")
            elif eff["type"] == "defense":
                self.defense_up = max(self.defense_up, eff.get("reduction", 0.5))
            eff["remaining"] -= 1
            if eff["remaining"] > 0:
                new.append(eff)
        self.status_effects = new
        return logs

    def check_flee(self):
        """Check if this member flees. Higher chance when lower HP."""
        if self.fled:
            return False
        hp_pct = self.hp / max(1, self.max_hp)
        if hp_pct > 0.25:
            return False
        # Below 25% HP: increasing flee chance
        # At 25%: 10% chance, at 10%: 35% chance, at 5%: 50% chance
        flee_chance = 0.10 + (0.25 - hp_pct) * 1.6
        flee_chance = min(0.50, flee_chance)
        if random.random() < flee_chance:
            self.fled = True
            return True
        return False


# ── Side (team of PackMembers) ──────────────────────────────────
class BattleSide:
    """One side of the battle — 1 or more PackMembers."""

    def __init__(self, dino_data, pack_size=1):
        self.dino_data = dino_data
        self.pack_size = pack_size
        self.family = SPECIES_FAMILIES.get(dino_data.get("id", "").lower(), "generic")
        self.dtype = dino_data.get("type", "carnivore")
        self.cw = dino_data.get("cw", 3000)

        # Create individual members
        self.members = []
        for i in range(pack_size):
            self.members.append(PackMember(dino_data, index=i if pack_size > 1 else 0))

        # Pack display name
        if pack_size > 1:
            self.display_name = f"Pack of {pack_size} {dino_data['name']}s"
        else:
            self.display_name = dino_data["name"]

        self.passive = PASSIVES.get(self.family)

    @property
    def alive_members(self):
        return [m for m in self.members if m.alive]

    @property
    def alive(self):
        return len(self.alive_members) > 0

    @property
    def alive_count(self):
        return len(self.alive_members)

    def apply_pack_bonuses(self):
        """Apply group passive bonuses to each member."""
        ally_count = self.pack_size - 1
        for m in self.members:
            if self.passive:
                pname = self.passive[0]
                if pname == "Lone Survivor" and self.pack_size == 1:
                    m.armor_bonus += 0.10
                elif pname == "Tyrant Roar":
                    m.atk_bonus += 0.10 * ally_count
                elif pname == "Pack Bark":
                    m.atk_bonus += 0.08 * ally_count
                elif pname == "Herd Shield":
                    m.armor_bonus += 0.05 * ally_count
                elif pname == "Shell Wall":
                    m.armor_bonus += 0.08 * ally_count

    def pick_attacker(self):
        """Pick a random alive member to attack this turn."""
        alive = self.alive_members
        if not alive:
            return None
        return random.choice(alive)

    def pick_target(self):
        """Pick a random alive member on this side to be attacked."""
        alive = self.alive_members
        if not alive:
            return None
        return random.choice(alive)


# ── Core Damage Calc (PoT Formula) ─────────────────────────────
def calc_pot_damage(attacker: PackMember, defender: PackMember, ability):
    """Returns (damage, zone_name, is_crit, is_dodge)"""
    if ability["base"] <= 0:
        return 0, "—", False, False

    # Dodge check
    has_bb = any(e["type"] == "bonebreak" for e in defender.status_effects)
    dodge = 0.02 if has_bb else min(0.15, defender.spd / 12000.0)
    if random.random() < dodge:
        return 0, "—", False, True

    # PoT formula
    cw_ratio = attacker.cw / max(1, defender.cw)
    raw = ability["base"] * cw_ratio

    # Hit zone
    zone, zmult = roll_hit_zone()
    raw *= zmult

    # Crit (12%)
    crit = random.random() < 0.12
    if crit:
        raw *= 1.5

    # Armor
    armor_total = defender.armor * (1.0 + defender.armor_bonus)
    raw *= (1.0 - min(0.50, armor_total * 0.10))

    # Defense stance
    if defender.defense_up > 0:
        raw *= (1.0 - min(0.90, defender.defense_up))

    # ATK bonus
    raw *= (1.0 + attacker.atk_bonus)

    # Variance ±20%
    raw *= random.uniform(0.80, 1.20)

    final = max(1, int(raw))
    defender.hp = max(0, defender.hp - final)
    return final, zone, crit, False


def apply_effects(ability, attacker: PackMember, defender: PackMember, lines):
    """Apply ability status effects. Returns log lines."""
    for eff in ability.get("effects", []):
        if eff["type"] == "bleed":
            dur = max(1, min(eff["dur"] + 2, int(eff["dur"] * (attacker.cw / max(1, defender.cw)))))
            defender.status_effects.append({"type": "bleed", "remaining": dur, "pct": eff.get("pct", 0.03)})
            lines.append(f"  🩸 {defender.label} starts **bleeding** ({dur} turns)!")
        elif eff["type"] == "bonebreak":
            dur = max(1, min(eff["dur"] + 2, int(eff["dur"] * (attacker.cw / max(1, defender.cw)))))
            defender.status_effects.append({"type": "bonebreak", "remaining": dur})
            lines.append(f"  🦴 {defender.label} suffers **Bonebreak** ({dur} turns)!")
        elif eff["type"] == "defense":
            attacker.status_effects.append({"type": "defense", "remaining": eff["dur"], "reduction": eff["reduction"]})
            lines.append(f"  🛡️ {attacker.label} enters **Defensive Stance** (−{int(eff['reduction']*100)}% incoming)")
        elif eff["type"] == "heal":
            heal = max(1, int(attacker.max_hp * eff.get("pct", 0.05)))
            attacker.hp = min(attacker.max_hp, attacker.hp + heal)
            lines.append(f"  💚 {attacker.label} heals **{heal}** HP → {attacker.hp}/{attacker.max_hp}")


# ── Battle Simulation ──────────────────────────────────────────
def simulate_battle(dino_a_data, dino_b_data, pack_a=1, pack_b=1, max_turns=15):
    """
    Turn-by-turn battle with individual pack members.
    Tracks prop bet outcomes: flee, bleed kills, first crit, KO count.
    """
    side_a = BattleSide(dino_a_data, pack_size=pack_a)
    side_b = BattleSide(dino_b_data, pack_size=pack_b)

    side_a.apply_pack_bonuses()
    side_b.apply_pack_bonuses()

    turns = []
    hp_snapshots = []  # [{a_hp, a_max, b_hp, b_max}, ...]
    turn_num = 0

    # Prop bet tracking
    any_fled = False
    bleed_kills = 0
    first_crit_side = None  # "a" or "b"
    total_kos = 0
    # Track which members have already been counted as dead
    dead_set = set()

    def _check_new_deaths(side, side_label):
        nonlocal total_kos
        count = 0
        for m in side.members:
            mid = id(m)
            if m.hp <= 0 and mid not in dead_set:
                dead_set.add(mid)
                total_kos += 1
                count += 1
        return count

    while side_a.alive and side_b.alive and turn_num < max_turns:
        turn_num += 1
        lines = [f"⚔️ **Turn {turn_num}**"]

        if side_a.pack_size > 1 or side_b.pack_size > 1:
            lines.append(f"  📋 {side_a.display_name}: {side_a.alive_count}/{side_a.pack_size} alive | "
                         f"{side_b.display_name}: {side_b.alive_count}/{side_b.pack_size} alive")

        # Status effect ticks
        for m in side_a.alive_members + side_b.alive_members:
            hp_before = m.hp
            for log_line in m.tick_status_effects():
                lines.append(log_line)
            # Bleed kill check
            if hp_before > 0 and m.hp <= 0:
                if any(e["type"] == "bleed" for e in m.status_effects) or hp_before > m.hp:
                    bleed_kills += 1

        # Check bleed deaths
        for m in side_a.members + side_b.members:
            if m.hp <= 0 and id(m) not in dead_set:
                lines.append(f"  💀 {m.label} succumbs to their wounds!")
                dead_set.add(id(m))
                total_kos += 1

        if not side_a.alive or not side_b.alive:
            turns.append(lines)
            break

        # Flee checks
        for m in side_a.alive_members + side_b.alive_members:
            if m.check_flee():
                any_fled = True
                lines.append(f"  🏃 {m.label} panics and **flees** the battle! ({m.hp}/{m.max_hp} HP)")

        if not side_a.alive or not side_b.alive:
            if not side_a.alive:
                lines.append(f"💨 {side_a.display_name}'s remaining fighters have fled or fallen!")
            if not side_b.alive:
                lines.append(f"💨 {side_b.display_name}'s remaining fighters have fled or fallen!")
            turns.append(lines)
            break

        # Speed-based initiative
        first_side, second_side = (side_a, side_b) if side_a.cw <= side_b.cw else (side_b, side_a)
        first_label = "a" if first_side is side_a else "b"
        second_label = "b" if first_label == "a" else "a"
        if random.random() < 0.15:
            first_side, second_side = second_side, first_side
            first_label, second_label = second_label, first_label

        # ── First side attacks ──
        attacker = first_side.pick_attacker()
        target = second_side.pick_target()
        if attacker and target:
            ability = attacker.pick_ability()
            attacker.cooldowns[ability["name"]] = ability["cd"]

            if ability["base"] > 0:
                dmg, zone, crit, dodge = calc_pot_damage(attacker, target, ability)
                if crit and first_crit_side is None:
                    first_crit_side = first_label
                if dodge:
                    lines.append(f"{attacker.label} uses **{ability['name']}** ({ability['desc']}) → {target.label} **dodges!** 💨")
                else:
                    crit_txt = " ⚡ **CRIT!**" if crit else ""
                    lines.append(
                        f"{attacker.label} uses **{ability['name']}** ({ability['desc']}) "
                        f"→ 🎯 {zone} HIT{crit_txt} — **{dmg}** damage"
                    )
                    lines.append(f"  ↳ {target.label}: {target.hp}/{target.max_hp} HP")
                    if target.hp <= 0:
                        lines.append(f"  💀 **{target.label}** has been defeated!")
                        _check_new_deaths(second_side, second_label)
            else:
                lines.append(f"{attacker.label} uses **{ability['name']}** ({ability['desc']})")

            apply_effects(ability, attacker, target, lines)

        if not second_side.alive:
            turns.append(lines)
            break

        # ── Second side attacks ──
        attacker2 = second_side.pick_attacker()
        target2 = first_side.pick_target()
        if attacker2 and target2:
            ability2 = attacker2.pick_ability()
            attacker2.cooldowns[ability2["name"]] = ability2["cd"]

            if ability2["base"] > 0:
                dmg2, zone2, crit2, dodge2 = calc_pot_damage(attacker2, target2, ability2)
                if crit2 and first_crit_side is None:
                    first_crit_side = second_label
                if dodge2:
                    lines.append(f"{attacker2.label} uses **{ability2['name']}** ({ability2['desc']}) → {target2.label} **dodges!** 💨")
                else:
                    crit_txt2 = " ⚡ **CRIT!**" if crit2 else ""
                    lines.append(
                        f"{attacker2.label} uses **{ability2['name']}** ({ability2['desc']}) "
                        f"→ 🎯 {zone2} HIT{crit_txt2} — **{dmg2}** damage"
                    )
                    lines.append(f"  ↳ {target2.label}: {target2.hp}/{target2.max_hp} HP")
                    if target2.hp <= 0:
                        lines.append(f"  💀 **{target2.label}** has been defeated!")
                        _check_new_deaths(first_side, first_label)
            else:
                lines.append(f"{attacker2.label} uses **{ability2['name']}** ({ability2['desc']})")

            apply_effects(ability2, attacker2, target2, lines)

        if not first_side.alive:
            turns.append(lines)
            break

        # Extra pack attacks
        for extra_side, target_side in [(first_side, second_side), (second_side, first_side)]:
            ex_label = "a" if extra_side is side_a else "b"
            extras = [m for m in extra_side.alive_members if m != attacker and m != attacker2]
            for em in extras:
                if not target_side.alive:
                    break
                if random.random() < 0.60:
                    et = target_side.pick_target()
                    if et:
                        ea = em.pick_ability()
                        em.cooldowns[ea["name"]] = ea["cd"]
                        if ea["base"] > 0:
                            ed, ez, ec, edg = calc_pot_damage(em, et, ea)
                            if ec and first_crit_side is None:
                                first_crit_side = ex_label
                            if edg:
                                lines.append(f"{em.label} also attacks with **{ea['name']}** → {et.label} **dodges!** 💨")
                            else:
                                ct = " ⚡ **CRIT!**" if ec else ""
                                lines.append(f"{em.label} also uses **{ea['name']}** → 🎯 {ez} HIT{ct} — **{ed}** dmg")
                                lines.append(f"  ↳ {et.label}: {et.hp}/{et.max_hp} HP")
                                if et.hp <= 0:
                                    lines.append(f"  💀 **{et.label}** has been defeated!")
                                    _check_new_deaths(target_side, "a" if target_side is side_a else "b")
                            apply_effects(ea, em, et, lines)

        if not side_a.alive or not side_b.alive:
            turns.append(lines)
            break

        # Turn summary
        a_hp = sum(m.hp for m in side_a.alive_members)
        a_max = sum(m.max_hp for m in side_a.members)
        b_hp = sum(m.hp for m in side_b.alive_members)
        b_max = sum(m.max_hp for m in side_b.members)
        lines.append(f"📊 {side_a.display_name}: {a_hp}/{a_max} HP ({side_a.alive_count} alive) | "
                     f"{side_b.display_name}: {b_hp}/{b_max} HP ({side_b.alive_count} alive)")
        hp_snapshots.append({"a_hp": a_hp, "a_max": a_max, "b_hp": b_hp, "b_max": b_max})

        for m in side_a.alive_members + side_b.alive_members:
            m.tick_cooldowns()

        turns.append(lines)

    # ── Winner ──
    if side_a.alive and not side_b.alive:
        winner = "a"
    elif side_b.alive and not side_a.alive:
        winner = "b"
    else:
        a_pct = sum(m.hp for m in side_a.alive_members) / max(1, sum(m.max_hp for m in side_a.members))
        b_pct = sum(m.hp for m in side_b.alive_members) / max(1, sum(m.max_hp for m in side_b.members))
        winner = "a" if a_pct >= b_pct else "b"

    w_side = side_a if winner == "a" else side_b
    l_side = side_b if winner == "a" else side_a

    a_passive = f"{side_a.passive[0]}: {side_a.passive[1]}" if side_a.passive else None
    b_passive = f"{side_b.passive[0]}: {side_b.passive[1]}" if side_b.passive else None

    return {
        "winner": winner,
        "winner_name": w_side.display_name,
        "loser_name": l_side.display_name,
        "turns": turns,
        "hp_snapshots": hp_snapshots,
        # Prop bet outcomes
        "any_fled": any_fled,
        "bleed_kills": bleed_kills,
        "first_crit_side": first_crit_side,
        "total_kos": total_kos,
        # Fighter stats
        "fighter_a": {
            "name": side_a.display_name, "family": side_a.family,
            "hp": sum(m.hp for m in side_a.alive_members),
            "max_hp": sum(m.max_hp for m in side_a.members),
            "type": side_a.dtype, "cw": side_a.cw,
            "group_slots": get_group_slots(side_a.cw),
            "pack_size": side_a.pack_size,
            "alive_count": side_a.alive_count,
            "fled_count": sum(1 for m in side_a.members if m.fled),
            "passive": a_passive,
        },
        "fighter_b": {
            "name": side_b.display_name, "family": side_b.family,
            "hp": sum(m.hp for m in side_b.alive_members),
            "max_hp": sum(m.max_hp for m in side_b.members),
            "type": side_b.dtype, "cw": side_b.cw,
            "group_slots": get_group_slots(side_b.cw),
            "pack_size": side_b.pack_size,
            "alive_count": side_b.alive_count,
            "fled_count": sum(1 for m in side_b.members if m.fled),
            "passive": b_passive,
        },
    }


