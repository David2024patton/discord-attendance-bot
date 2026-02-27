"""
Battle Engine for Path of Titans Primordial Tyrants combat simulation.

Implements:
- Weight-scaled damage (attacker.cw / target.cw ratio)
- Species-specific abilities with cooldowns and status effects
- Pack slot system (10 slots total, T-Rex=4 slots, Raptor=1 slot)
- Group bonuses (Tyrant Roar, Bark stacking per ally)
- Status effects: Bleed (% HP DoT), Bonebreak (halved speed/dodge)
- Solo passives (Lone Survivor: +10% armor when alone)
- Multi-round combat with round-by-round log
"""

import random
import json
import os
import math

# ── Species Family Mapping ──────────────────────────────────────
# Maps dino IDs to their species family for group buff logic
SPECIES_FAMILIES = {
    # Tyrannosaurids — get Tyrant Roar group buff
    "tyrannosaurus": "tyrannosaurid",
    "giganotosaurus": "tyrannosaurid",
    "alioramus": "tyrannosaurid",
    "daspletosaurus": "tyrannosaurid",
    "tarbosaurus": "tyrannosaurid",
    "gorgosaurus": "tyrannosaurid",
    "albertosaurus": "tyrannosaurid",
    "yutyrannus": "tyrannosaurid",

    # Raptors — get Bark group buff
    "utahraptor": "raptor",
    "achillobator": "raptor",
    "deinonychus": "raptor",
    "latenivenatrix": "raptor",
    "concavenator": "raptor",

    # Ceratopsians — get Herd Shield group buff
    "triceratops": "ceratopsian",
    "albertaceratops": "ceratopsian",
    "styracosaurus": "ceratopsian",
    "pachyrhinosaurus": "ceratopsian",
    "ceratosaurus": "ceratopsian",
    "diabloceratops": "ceratopsian",
    "einiosaurus": "ceratopsian",
    "kosmoceratops": "ceratopsian",
    "medusaceratops": "ceratopsian",
    "nasutoceratops": "ceratopsian",
    "regaliceratops": "ceratopsian",
    "sinoceratops": "ceratopsian",
    "torosaurus": "ceratopsian",
    "zuniceratops": "ceratopsian",

    # Hadrosaurs — get Alarm Call group buff
    "lambeosaurus": "hadrosaur",
    "parasaurolophus": "hadrosaur",
    "iguanodon": "hadrosaur",
    "barsboldia": "hadrosaur",
    "camptosaurus": "hadrosaur",
    "corythosaurus": "hadrosaur",
    "edmontosaurus": "hadrosaur",
    "maiasaura": "hadrosaur",
    "olorotitan": "hadrosaur",
    "saurolophus": "hadrosaur",

    # Ankylosaurs — get Shell Wall group buff
    "anodontosaurus": "ankylosaur",
    "ankylosaurus": "ankylosaur",
    "kentrosaurus": "ankylosaur",
    "stegosaurus": "ankylosaur",

    # Sauropods — get Tremor group buff
    "amargasaurus": "sauropod",
    "deinocheirus": "sauropod",

    # Therizinosaurs — solo specialists
    "therizinosaurus": "therizinosaur",
}

# ── Group Slot Defaults By Weight Class ─────────────────────────
def get_group_slots(dino):
    """Determine group slots based on combat weight. Lower slots = bigger packs."""
    cw = dino.get("cw", 3000)
    if cw >= 7000:
        return 5   # Apex: max 2 per team
    elif cw >= 5000:
        return 4   # Large: max 2-3
    elif cw >= 3000:
        return 3   # Medium: max 3
    elif cw >= 1500:
        return 2   # Small: max 5
    else:
        return 1   # Tiny: max 10


# ── Default Abilities By Species ────────────────────────────────
def get_abilities(dino):
    """Generate abilities based on species family and weight class."""
    dino_id = dino.get("id", "").lower()
    family = SPECIES_FAMILIES.get(dino_id, "generic")
    cw = dino.get("cw", 3000)
    atk = dino.get("atk", 50)
    dtype = dino.get("type", "carnivore")

    abilities = []

    if family == "tyrannosaurid":
        abilities = [
            {"name": "Bite", "damage": int(atk * 1.0), "cooldown": 0, "effects": []},
            {"name": "Charged Bite", "damage": int(atk * 2.2), "cooldown": 3, "effects": []},
            {"name": "Crushing Bite", "damage": int(atk * 1.5), "cooldown": 2,
             "effects": [{"type": "bonebreak", "duration": 2, "value": 0.25}]},
        ]
    elif family == "raptor":
        abilities = [
            {"name": "Claw Slash", "damage": int(atk * 0.8), "cooldown": 0, "effects": []},
            {"name": "Pounce", "damage": int(atk * 1.8), "cooldown": 2,
             "effects": [{"type": "bleed", "duration": 3, "value": 0.03}]},
            {"name": "Pack Frenzy", "damage": int(atk * 1.2), "cooldown": 1,
             "effects": [{"type": "bleed", "duration": 2, "value": 0.02}]},
        ]
    elif family == "therizinosaur":
        abilities = [
            {"name": "Scythe Swipe", "damage": int(atk * 1.2), "cooldown": 0, "effects": []},
            {"name": "Raise Your Claws", "damage": 0, "cooldown": 3,
             "effects": [{"type": "defense_stance", "duration": 2, "value": 0.9}]},
            {"name": "Rending Slash", "damage": int(atk * 2.0), "cooldown": 2,
             "effects": [{"type": "bleed", "duration": 3, "value": 0.04}]},
        ]
    elif family == "ceratopsian":
        abilities = [
            {"name": "Horn Thrust", "damage": int(atk * 1.0), "cooldown": 0, "effects": []},
            {"name": "Charge", "damage": int(atk * 2.0), "cooldown": 3,
             "effects": [{"type": "bonebreak", "duration": 1, "value": 0.15}]},
            {"name": "Headbutt", "damage": int(atk * 1.3), "cooldown": 1, "effects": []},
        ]
    elif family == "ankylosaur":
        abilities = [
            {"name": "Tail Club", "damage": int(atk * 1.1), "cooldown": 0, "effects": []},
            {"name": "Tail Slam", "damage": int(atk * 1.8), "cooldown": 2,
             "effects": [{"type": "bonebreak", "duration": 2, "value": 0.2}]},
            {"name": "Spike Guard", "damage": int(atk * 0.5), "cooldown": 2,
             "effects": [{"type": "defense_stance", "duration": 1, "value": 0.5}]},
        ]
    elif family == "hadrosaur":
        abilities = [
            {"name": "Kick", "damage": int(atk * 0.9), "cooldown": 0, "effects": []},
            {"name": "Tail Sweep", "damage": int(atk * 1.4), "cooldown": 2, "effects": []},
            {"name": "Alarm Call", "damage": 0, "cooldown": 3,
             "effects": [{"type": "group_heal", "duration": 0, "value": 0.05}]},
        ]
    elif family == "sauropod":
        abilities = [
            {"name": "Stomp", "damage": int(atk * 1.0), "cooldown": 0, "effects": []},
            {"name": "Tail Whip", "damage": int(atk * 1.6), "cooldown": 2, "effects": []},
            {"name": "Tremor", "damage": int(atk * 0.6), "cooldown": 3,
             "effects": [{"type": "bonebreak", "duration": 1, "value": 0.1}]},
        ]
    else:
        # Generic abilities based on diet
        if dtype == "carnivore":
            abilities = [
                {"name": "Bite", "damage": int(atk * 1.0), "cooldown": 0, "effects": []},
                {"name": "Lunge", "damage": int(atk * 1.6), "cooldown": 2,
                 "effects": [{"type": "bleed", "duration": 2, "value": 0.02}]},
            ]
        else:
            abilities = [
                {"name": "Kick", "damage": int(atk * 0.9), "cooldown": 0, "effects": []},
                {"name": "Tail Sweep", "damage": int(atk * 1.5), "cooldown": 2, "effects": []},
            ]

    return abilities


def get_passives(dino):
    """Generate passives based on species family."""
    dino_id = dino.get("id", "").lower()
    family = SPECIES_FAMILIES.get(dino_id, "generic")

    passives = []
    if family == "therizinosaur":
        passives.append({
            "name": "Lone Survivor",
            "description": "+10% Armor, x2 Acceleration when solo",
            "modifier": {"armor_bonus": 0.10, "solo_only": True}
        })
    elif family == "tyrannosaurid":
        passives.append({
            "name": "Tyrant Roar",
            "description": "+10% ATK per Tyrannosaurid ally in group",
            "modifier": {"atk_per_ally": 0.10, "family": "tyrannosaurid"}
        })
    elif family == "raptor":
        passives.append({
            "name": "Pack Bark",
            "description": "+8% ATK per Raptor ally in group",
            "modifier": {"atk_per_ally": 0.08, "family": "raptor"}
        })
    elif family == "ceratopsian":
        passives.append({
            "name": "Herd Shield",
            "description": "+5% Armor per Ceratopsian ally",
            "modifier": {"armor_per_ally": 0.05, "family": "ceratopsian"}
        })
    elif family == "ankylosaur":
        passives.append({
            "name": "Shell Wall",
            "description": "+8% Armor per Ankylosaur ally",
            "modifier": {"armor_per_ally": 0.08, "family": "ankylosaur"}
        })

    return passives


# ── Fighter Class ───────────────────────────────────────────────
class DinoFighter:
    """Wraps a dino profile with combat state."""

    def __init__(self, dino_data):
        self.data = dino_data
        self.name = dino_data["name"]
        self.dino_id = dino_data.get("id", "unknown")
        self.family = SPECIES_FAMILIES.get(self.dino_id.lower(), "generic")
        self.dtype = dino_data.get("type", "carnivore")

        # Base stats
        self.max_hp = dino_data.get("hp", 500)
        self.hp = self.max_hp
        self.cw = dino_data.get("cw", 3000)
        self.base_atk = dino_data.get("atk", 50)
        self.base_armor = dino_data.get("armor", 1.0)
        self.spd = dino_data.get("spd", 500)
        self.group_slots = get_group_slots(dino_data)

        # Combat modifiers (applied from passives/group)
        self.atk_bonus = 0.0      # percentage bonus
        self.armor_bonus = 0.0    # percentage bonus

        # Abilities
        self.abilities = get_abilities(dino_data)
        self.passives = get_passives(dino_data)
        self.cooldowns = {a["name"]: 0 for a in self.abilities}

        # Status effects: list of {type, remaining_duration, value}
        self.status_effects = []

        # Defense stance reduction (0.0 = none, 0.9 = 90% reduction)
        self.defense_stance = 0.0

    @property
    def alive(self):
        return self.hp > 0

    @property
    def effective_atk(self):
        return self.base_atk * (1.0 + self.atk_bonus)

    @property
    def effective_armor(self):
        return self.base_armor * (1.0 + self.armor_bonus)

    def pick_ability(self):
        """Pick the best available ability (highest damage off cooldown)."""
        available = [a for a in self.abilities if self.cooldowns.get(a["name"], 0) <= 0]
        if not available:
            available = [a for a in self.abilities if a["cooldown"] == 0]
        if not available:
            return {"name": "Struggle", "damage": max(5, int(self.base_atk * 0.3)),
                    "cooldown": 0, "effects": []}

        # Weighted random: prefer higher damage abilities but add randomness
        weights = []
        for a in available:
            w = max(1, a["damage"]) + random.randint(0, 20)
            # Boost healing/defense abilities when low HP
            if self.hp < self.max_hp * 0.3:
                for eff in a.get("effects", []):
                    if eff["type"] in ("defense_stance", "group_heal"):
                        w += 30
            weights.append(w)

        total = sum(weights)
        r = random.random() * total
        cumulative = 0
        for i, w in enumerate(weights):
            cumulative += w
            if r <= cumulative:
                return available[i]
        return available[0]

    def apply_group_buffs(self, allies):
        """Apply passive group bonuses based on allies."""
        for passive in self.passives:
            mod = passive.get("modifier", {})

            # Solo-only bonuses
            if mod.get("solo_only") and len(allies) == 0:
                self.armor_bonus += mod.get("armor_bonus", 0)

            # ATK per ally of same family
            if "atk_per_ally" in mod:
                family = mod.get("family", "")
                ally_count = sum(1 for a in allies if a.family == family and a.alive)
                self.atk_bonus += mod["atk_per_ally"] * ally_count

            # Armor per ally of same family
            if "armor_per_ally" in mod:
                family = mod.get("family", "")
                ally_count = sum(1 for a in allies if a.family == family and a.alive)
                self.armor_bonus += mod["armor_per_ally"] * ally_count

    def tick_status_effects(self):
        """Process status effects at start of round. Returns log entries."""
        logs = []
        new_effects = []
        for eff in self.status_effects:
            if eff["type"] == "bleed":
                bleed_dmg = int(self.max_hp * eff["value"])
                self.hp = max(0, self.hp - bleed_dmg)
                logs.append(f"  🩸 {self.name} bleeds for {bleed_dmg} damage ({eff['remaining']} rounds left)")
            elif eff["type"] == "bonebreak":
                logs.append(f"  🦴 {self.name} is bonebroken — dodge reduced")

            eff["remaining"] -= 1
            if eff["remaining"] > 0:
                new_effects.append(eff)

        # Clear defense stance each round (must recast)
        self.defense_stance = 0.0
        self.status_effects = new_effects
        return logs

    def tick_cooldowns(self):
        """Reduce all ability cooldowns by 1."""
        for name in self.cooldowns:
            if self.cooldowns[name] > 0:
                self.cooldowns[name] -= 1

    def receive_damage(self, raw_damage, attacker_cw):
        """Calculate and apply damage with weight scaling and armor."""
        # Weight scaling: heavier attacker does proportionally more damage
        weight_ratio = attacker_cw / max(1, self.cw)
        weight_mult = min(3.0, max(0.3, weight_ratio))

        # Armor reduction: armor 1.0 = 10% reduction, armor 2.0 = 20%
        armor_reduction = min(0.6, self.effective_armor * 0.10)

        # Bonebreak check: reduced dodge
        has_bonebreak = any(e["type"] == "bonebreak" for e in self.status_effects)
        dodge_chance = 0.05 if has_bonebreak else (min(0.15, self.spd / 10000.0))

        # Defense stance
        if self.defense_stance > 0:
            armor_reduction += self.defense_stance

        # Random variance
        variance = random.uniform(0.8, 1.2)

        # Final damage
        damage = raw_damage * weight_mult * variance * (1.0 - min(0.9, armor_reduction))
        damage = max(1, int(damage))

        # Dodge check
        if random.random() < dodge_chance:
            return 0, True  # dodged

        self.hp = max(0, self.hp - damage)
        return damage, False


# ── Battle Simulation ───────────────────────────────────────────
def simulate_battle(dino_a_data, dino_b_data, max_rounds=8):
    """
    Simulate a 1v1 battle between two dinosaurs.

    Returns:
        dict with keys:
        - winner: "a" | "b" | "tie"
        - winner_name: str
        - loser_name: str
        - rounds: list of round log strings
        - fighter_a: {name, hp, max_hp, abilities_used}
        - fighter_b: {name, hp, max_hp, abilities_used}
    """
    a = DinoFighter(dino_a_data)
    b = DinoFighter(dino_b_data)

    # Apply solo passives (1v1 = no allies)
    a.apply_group_buffs([])
    b.apply_group_buffs([])

    rounds = []
    abilities_used_a = {}
    abilities_used_b = {}

    for round_num in range(1, max_rounds + 1):
        round_log = [f"⚔️ **Round {round_num}**"]

        # Tick status effects
        a_status_logs = a.tick_status_effects()
        b_status_logs = b.tick_status_effects()
        round_log.extend(a_status_logs)
        round_log.extend(b_status_logs)

        if not a.alive or not b.alive:
            rounds.append(round_log)
            break

        # Determine turn order by speed (faster goes first)
        first, second = (a, b) if a.spd >= b.spd else (b, a)
        first_label = "a" if first is a else "b"

        # First attacker's turn
        ability = first.pick_ability()
        first.cooldowns[ability["name"]] = ability["cooldown"]
        ability_track = abilities_used_a if first is a else abilities_used_b
        ability_track[ability["name"]] = ability_track.get(ability["name"], 0) + 1

        if ability["damage"] > 0:
            dmg, dodged = second.receive_damage(ability["damage"], first.cw)
            if dodged:
                round_log.append(f"  {first.name} uses **{ability['name']}** — {second.name} dodges!")
            else:
                round_log.append(f"  {first.name} uses **{ability['name']}** for **{dmg}** damage → {second.name} HP: {second.hp}/{second.max_hp}")
        else:
            round_log.append(f"  {first.name} uses **{ability['name']}**!")

        # Apply ability effects
        for eff in ability.get("effects", []):
            if eff["type"] in ("bleed", "bonebreak"):
                second.status_effects.append({
                    "type": eff["type"],
                    "remaining": eff["duration"],
                    "value": eff["value"]
                })
                icon = "🩸" if eff["type"] == "bleed" else "🦴"
                round_log.append(f"  {icon} {second.name} is afflicted with **{eff['type'].title()}** for {eff['duration']} rounds!")
            elif eff["type"] == "defense_stance":
                first.defense_stance = eff["value"]
                round_log.append(f"  🛡️ {first.name} enters **Defensive Stance** (−{int(eff['value']*100)}% incoming damage)")
            elif eff["type"] == "group_heal":
                heal = int(first.max_hp * eff["value"])
                first.hp = min(first.max_hp, first.hp + heal)
                round_log.append(f"  💚 {first.name} heals for **{heal}** HP → HP: {first.hp}/{first.max_hp}")

        if not second.alive:
            round_log.append(f"  💀 **{second.name} has been defeated!**")
            rounds.append(round_log)
            break

        # Second attacker's turn
        ability2 = second.pick_ability()
        second.cooldowns[ability2["name"]] = ability2["cooldown"]
        ability_track2 = abilities_used_b if second is b else abilities_used_a
        ability_track2[ability2["name"]] = ability_track2.get(ability2["name"], 0) + 1

        if ability2["damage"] > 0:
            dmg2, dodged2 = first.receive_damage(ability2["damage"], second.cw)
            if dodged2:
                round_log.append(f"  {second.name} uses **{ability2['name']}** — {first.name} dodges!")
            else:
                round_log.append(f"  {second.name} uses **{ability2['name']}** for **{dmg2}** damage → {first.name} HP: {first.hp}/{first.max_hp}")
        else:
            round_log.append(f"  {second.name} uses **{ability2['name']}**!")

        # Apply ability effects
        for eff in ability2.get("effects", []):
            if eff["type"] in ("bleed", "bonebreak"):
                first.status_effects.append({
                    "type": eff["type"],
                    "remaining": eff["duration"],
                    "value": eff["value"]
                })
                icon = "🩸" if eff["type"] == "bleed" else "🦴"
                round_log.append(f"  {icon} {first.name} is afflicted with **{eff['type'].title()}** for {eff['duration']} rounds!")
            elif eff["type"] == "defense_stance":
                second.defense_stance = eff["value"]
                round_log.append(f"  🛡️ {second.name} enters **Defensive Stance** (−{int(eff['value']*100)}% incoming damage)")
            elif eff["type"] == "group_heal":
                heal = int(second.max_hp * eff["value"])
                second.hp = min(second.max_hp, second.hp + heal)
                round_log.append(f"  💚 {second.name} heals for **{heal}** HP → HP: {second.hp}/{second.max_hp}")

        if not first.alive:
            round_log.append(f"  💀 **{first.name} has been defeated!**")
            rounds.append(round_log)
            break

        # HP summary
        round_log.append(f"  📊 {a.name}: {a.hp}/{a.max_hp} HP | {b.name}: {b.hp}/{b.max_hp} HP")

        # Tick cooldowns
        a.tick_cooldowns()
        b.tick_cooldowns()

        rounds.append(round_log)

    # Determine winner
    if a.alive and not b.alive:
        winner = "a"
    elif b.alive and not a.alive:
        winner = "b"
    elif a.hp / max(1, a.max_hp) >= b.hp / max(1, b.max_hp):
        winner = "a"  # Higher HP% wins tiebreaker
    else:
        winner = "b"

    winner_name = a.name if winner == "a" else b.name
    loser_name = b.name if winner == "a" else a.name

    return {
        "winner": winner,
        "winner_name": winner_name,
        "loser_name": loser_name,
        "rounds": rounds,
        "fighter_a": {
            "name": a.name,
            "family": a.family,
            "hp": a.hp,
            "max_hp": a.max_hp,
            "type": a.dtype,
            "cw": a.cw,
            "abilities_used": abilities_used_a,
            "group_slots": a.group_slots,
        },
        "fighter_b": {
            "name": b.name,
            "family": b.family,
            "hp": b.hp,
            "max_hp": b.max_hp,
            "type": b.dtype,
            "cw": b.cw,
            "abilities_used": abilities_used_b,
            "group_slots": b.group_slots,
        },
    }
