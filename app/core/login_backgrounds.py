"""Login background configurations"""

SOLID_COLORS = {
    # DC Heroes
    "superman_classic": {"name": "Superman Classic", "gradient": "linear-gradient(135deg, #dc143c 0%, #0066cc 100%)", "group": "DC Heroes"},
    "wonder_woman": {"name": "Wonder Woman", "gradient": "linear-gradient(135deg, #dc143c 0%, #ffd700 50%, #0066cc 100%)", "group": "DC Heroes"},
    "batman_gotham": {"name": "Batman Gotham", "gradient": "linear-gradient(135deg, #000000 0%, #2f4f4f 50%, #ffd700 100%)", "group": "DC Heroes"},
    "batman_70s": {"name": "Batman (70s)", "gradient": "linear-gradient(135deg, #4169e1 0%, #c0c0c0 50%, #ffd700 100%)", "group": "DC Heroes"},
    "nightwing_blue": {"name": "Nightwing", "gradient": "linear-gradient(135deg, #000080 0%, #1e90ff 100%)", "group": "DC Heroes"},
    "robin_traffic": {"name": "Robin Traffic Light", "gradient": "linear-gradient(135deg, #dc143c 0%, #ffd700 50%, #228b22 100%)", "group": "DC Heroes"},
    "red_hood": {"name": "Red Hood", "gradient": "linear-gradient(135deg, #8b0000 0%, #000000 100%)", "group": "DC Heroes"},
    "batgirl_purple": {"name": "Batgirl/Oracle", "gradient": "linear-gradient(135deg, #4b0082 0%, #ffd700 100%)", "group": "DC Heroes"},
    "batwoman_crimson": {"name": "Batwoman", "gradient": "linear-gradient(135deg, #8b0000 0%, #000000 50%, #8b0000 100%)", "group": "DC Heroes"},
    "green_arrow": {"name": "Green Arrow", "gradient": "linear-gradient(135deg, #228b22 0%, #8b4513 100%)", "group": "DC Heroes"},
    "kryptonian_blue": {"name": "Kryptonian Blue", "gradient": "linear-gradient(135deg, #003d7a 0%, #0066cc 100%)", "group": "DC Heroes"},
    "scarlet_speedster": {"name": "Scarlet Speedster (Flash)", "gradient": "linear-gradient(135deg, #8b0000 0%, #dc143c 100%)", "group": "DC Heroes"},
    "shazam_thunder": {"name": "Shazam", "gradient": "linear-gradient(135deg, #dc143c 0%, #ffd700 100%)", "group": "DC Heroes"},
    "blue_beetle": {"name": "Blue Beetle", "gradient": "linear-gradient(135deg, #000080 0%, #4169e1 100%)", "group": "DC Heroes"},
    "booster_gold": {"name": "Booster Gold", "gradient": "linear-gradient(135deg, #0066cc 0%, #ffd700 100%)", "group": "DC Heroes"},
    "cyborg_tech": {"name": "Cyborg", "gradient": "linear-gradient(135deg, #708090 0%, #dc143c 100%)", "group": "DC Heroes"},
    "stargirl": {"name": "Stargirl", "gradient": "linear-gradient(135deg, #dc143c 0%, #ffffff 50%, #0066cc 100%)", "group": "DC Heroes"},
    "hawkman_wings": {"name": "Hawkman", "gradient": "linear-gradient(135deg, #8b4513 0%, #ffd700 100%)", "group": "DC Heroes"},
    "martian_manhunter": {"name": "Martian Manhunter", "gradient": "linear-gradient(135deg, #228b22 0%, #dc143c 100%)", "group": "DC Heroes"},
    "teen_titans": {"name": "Teen Titans", "gradient": "linear-gradient(135deg, #dc143c 0%, #ffffff 50%, #dc143c 100%)", "group": "DC Heroes"},

    # Marvel Heroes
    "hulk_gamma": {"name": "Hulk Gamma", "gradient": "linear-gradient(135deg, #145214 0%, #228b22 100%)", "group": "Marvel Heroes"},
    "iron_gold": {"name": "Iron Man Gold", "gradient": "linear-gradient(135deg, #8b0000 0%, #ffd700 100%)", "group": "Marvel Heroes"},
    "mjolnir_silver": {"name": "Mjolnir Silver", "gradient": "linear-gradient(135deg, #808080 0%, #c0c0c0 100%)", "group": "Marvel Heroes"},
    "wakanda_purple": {"name": "Wakanda Purple", "gradient": "linear-gradient(135deg, #6a4db3 0%, #9370db 100%)", "group": "Marvel Heroes"},
    "spider_gwen": {"name": "Spider-Gwen", "gradient": "linear-gradient(135deg, #ffffff 0%, #00d4ff 50%, #ff1493 100%)", "group": "Marvel Heroes"},
    "scarlet_spider": {"name": "Scarlet Spider (90s)", "gradient": "linear-gradient(135deg, #dc143c 0%, #dc143c 40%, #0066cc 60%, #dc143c 100%)", "group": "Marvel Heroes"},
    "nova_corps": {"name": "Nova Corps", "gradient": "linear-gradient(135deg, #b8860b 0%, #ffd700 100%)", "group": "Marvel Heroes"},
    "daredevil_red": {"name": "Daredevil Red", "gradient": "linear-gradient(135deg, #8b0000 0%, #dc143c 100%)", "group": "Marvel Heroes"},
    "daredevil_yellow": {"name": "Daredevil (Yellow)", "gradient": "linear-gradient(135deg, #ffd700 0%, #ffd700 60%, #8b0000 80%, #ffd700 100%)", "group": "Marvel Heroes"},
    "nightcrawler": {"name": "Nightcrawler", "gradient": "linear-gradient(135deg, #191970 0%, #4169e1 100%)", "group": "Marvel Heroes"},
    "x_men_gold": {"name": "X-Men Gold", "gradient": "linear-gradient(135deg, #ffd700 0%, #0066cc 100%)", "group": "Marvel Heroes"},
    "cable": {"name": "Cable", "gradient": "linear-gradient(135deg, #4169e1 0%, #708090 40%, #ffd700 100%)", "group": "Marvel Heroes"},
    "wolverine_classic": {"name": "Wolverine (Classic)", "gradient": "linear-gradient(135deg, #ffd700 0%, #0066cc 50%, #ffd700 100%)", "group": "Marvel Heroes"},
    "wolverine_brown": {"name": "Wolverine (Brown)", "gradient": "linear-gradient(135deg, #d2691e 0%, #8b4513 50%, #d2691e 100%)", "group": "Marvel Heroes"},
    "colossus": {"name": "Colossus", "gradient": "linear-gradient(135deg, #e8e8e8 0%, #c0c0c0 25%, #dc143c 50%, #ffd700 75%, #c0c0c0 100%)", "group": "Marvel Heroes"},

    # DC Villains
    "joker_madness": {"name": "Joker Madness", "gradient": "linear-gradient(135deg, #6a0dad 0%, #32cd32 100%)", "group": "DC Villains"},
    "harley_chaos": {"name": "Harley Chaos", "gradient": "linear-gradient(135deg, #ff1493 0%, #000000 50%, #00bfff 100%)", "group": "DC Villains"},
    "lex_luthor": {"name": "Lex Luthor", "gradient": "linear-gradient(135deg, #32cd32 0%, #4b0082 100%)", "group": "DC Villains"},
    "darkseid_omega": {"name": "Darkseid", "gradient": "linear-gradient(135deg, #2f4f4f 0%, #dc143c 100%)", "group": "DC Villains"},
    "brainiac": {"name": "Brainiac", "gradient": "linear-gradient(135deg, #32cd32 0%, #9370db 100%)", "group": "DC Villains"},
    "deathstroke": {"name": "Deathstroke", "gradient": "linear-gradient(135deg, #ff4500 0%, #000000 50%, #0066cc 100%)", "group": "DC Villains"},
    "reverse_flash": {"name": "Reverse Flash", "gradient": "linear-gradient(135deg, #ffff00 0%, #dc143c 100%)", "group": "DC Villains"},
    "sinestro": {"name": "Sinestro", "gradient": "linear-gradient(135deg, #ffff00 0%, #9370db 100%)", "group": "DC Villains"},
    "black_adam": {"name": "Black Adam", "gradient": "linear-gradient(135deg, #000000 0%, #ffd700 100%)", "group": "DC Villains"},
    "killer_croc": {"name": "Killer Croc", "gradient": "linear-gradient(135deg, #228b22 0%, #2f4f4f 100%)", "group": "DC Villains"},
    "mr_freeze": {"name": "Mr. Freeze", "gradient": "linear-gradient(135deg, #87ceeb 0%, #4682b4 100%)", "group": "DC Villains"},
    "riddler": {"name": "The Riddler", "gradient": "linear-gradient(135deg, #32cd32 0%, #4b0082 100%)", "group": "DC Villains"},
    "two_face": {"name": "Two-Face", "gradient": "linear-gradient(90deg, #ffffff 0%, #ffffff 50%, #000000 50%, #000000 100%)", "group": "DC Villains"},
    "two_face_classic": {"name": "Two-Face (Classic)", "gradient": "linear-gradient(90deg, #f5d6ba 0%, #f5d6ba 50%, #7d5a87 50%, #6b8e4e 100%)", "group": "DC Villains"},
    "penguin": {"name": "The Penguin", "gradient": "linear-gradient(135deg, #000000 0%, #4b0082 100%)", "group": "DC Villains"},
    "bane_venom": {"name": "Bane", "gradient": "linear-gradient(135deg, #228b22 0%, #000000 100%)", "group": "DC Villains"},


    # Marvel Villains & Anti-Heroes
    "venom_black": {"name": "Venom Black", "gradient": "linear-gradient(135deg, #0a0a0a 0%, #1a1a1a 100%)", "group": "Marvel Villains & Anti-Heroes"},
    "symbiote_swirl": {"name": "Symbiote Swirl", "gradient": "linear-gradient(135deg, #000000 0%, #1a1a1a 50%, #000000 100%)", "group": "Marvel Villains & Anti-Heroes"},
    "carnage_chaos": {"name": "Carnage Chaos", "gradient": "linear-gradient(135deg, #8b0000 0%, #dc143c 50%, #ff0000 100%)", "group": "Marvel Villains & Anti-Heroes"},
    "deadpool_merc": {"name": "Deadpool Merc", "gradient": "linear-gradient(135deg, #8b0000 0%, #000000 50%, #8b0000 100%)", "group": "Marvel Villains & Anti-Heroes"},
    "green_goblin": {"name": "Green Goblin", "gradient": "linear-gradient(135deg, #228b22 0%, #6a0dad 100%)", "group": "Marvel Villains & Anti-Heroes"},
    "hobgoblin": {"name": "Hobgoblin", "gradient": "linear-gradient(135deg, #ff6600 0%, #4b0082 100%)", "group": "Marvel Villains & Anti-Heroes"},
    "demogoblin": {"name": "Demogoblin", "gradient": "linear-gradient(135deg, #1a0a00 0%, #8b0000 40%, #ff4500 70%, #ffa500 100%)", "group": "Marvel Villains & Anti-Heroes"},
    "magneto_master": {"name": "Magneto Master", "gradient": "linear-gradient(135deg, #800080 0%, #dc143c 100%)", "group": "Marvel Villains & Anti-Heroes"},
    "punisher_skull": {"name": "Punisher Skull", "gradient": "linear-gradient(135deg, #0a0a0a 0%, #2f2f2f 100%)", "group": "Marvel Villains & Anti-Heroes"},
    "ghost_rider": {"name": "Ghost Rider Flame", "gradient": "linear-gradient(135deg, #000000 0%, #ff4500 50%, #ff8c00 100%)", "group": "Marvel Villains & Anti-Heroes"},
    "elektra_crimson": {"name": "Elektra Crimson", "gradient": "linear-gradient(135deg, #800000 0%, #dc143c 100%)", "group": "Marvel Villains & Anti-Heroes"},
    "poison_ivy": {"name": "Poison Ivy", "gradient": "linear-gradient(135deg, #228b22 0%, #8b0000 100%)", "group": "Marvel Villains & Anti-Heroes"},
    "mystique_blue": {"name": "Mystique Blue", "gradient": "linear-gradient(135deg, #000080 0%, #4169e1 100%)", "group": "Marvel Villains & Anti-Heroes"},
    "thanos_titan": {"name": "Thanos Titan", "gradient": "linear-gradient(135deg, #4b0082 0%, #6a0dad 50%, #b8860b 100%)", "group": "Marvel Villains & Anti-Heroes"},
    "apocalypse": {"name": "Apocalypse", "gradient": "linear-gradient(135deg, #4682b4 0%, #708090 100%)", "group": "Marvel Villains & Anti-Heroes"},

    # Lantern Corps
    "lantern_green": {"name": "Lantern Green", "gradient": "linear-gradient(135deg, #00b32c 0%, #00ff00 100%)", "group": "Lantern Corps"},
    "red_lantern": {"name": "Red Lantern (Rage)", "gradient": "linear-gradient(135deg, #8b0000 0%, #ff0000 100%)", "group": "Lantern Corps"},
    "blue_lantern": {"name": "Blue Lantern (Hope)", "gradient": "linear-gradient(135deg, #1e90ff 0%, #87ceeb 100%)", "group": "Lantern Corps"},
    "sinestro_corps": {"name": "Sinestro Corps", "gradient": "linear-gradient(135deg, #cccc00 0%, #ffff00 100%)", "group": "Lantern Corps"},
    "star_sapphire": {"name": "Star Sapphire (Love)", "gradient": "linear-gradient(135deg, #9370db 0%, #ff1493 100%)", "group": "Lantern Corps"},
    "indigo_tribe": {"name": "Indigo Tribe", "gradient": "linear-gradient(135deg, #4b0082 0%, #9400d3 100%)", "group": "Lantern Corps"},
    "agent_orange": {"name": "Agent Orange (Avarice)", "gradient": "linear-gradient(135deg, #ff4500 0%, #ff8c00 100%)", "group": "Lantern Corps"},
    "white_lantern": {"name": "White Lantern (Life)", "gradient": "linear-gradient(135deg, #ffffff 0%, #f0f8ff 50%, #ffffff 100%)", "group": "Lantern Corps"},
    "black_lantern": {"name": "Black Lantern (Death)", "gradient": "linear-gradient(135deg, #000000 0%, #1c1c1c 50%, #000000 100%)", "group": "Lantern Corps"},

    # DC Dark/Mystical
    "zatanna_magic": {"name": "Zatanna", "gradient": "linear-gradient(135deg, #000000 0%, #9370db 100%)", "group": "DC Dark/Mystical"},
    "constantine_trench": {"name": "Constantine", "gradient": "linear-gradient(135deg, #8b7355 0%, #2f2f2f 100%)", "group": "DC Dark/Mystical"},
    "swamp_thing": {"name": "Swamp Thing", "gradient": "linear-gradient(135deg, #228b22 0%, #8b4513 100%)", "group": "DC Dark/Mystical"},
    "sandman_dream": {"name": "Sandman (Dream)", "gradient": "linear-gradient(135deg, #000000 0%, #4b0082 50%, #000000 100%)"},
    "rorschach": {"name": "Rorschach", "gradient": "linear-gradient(135deg, #000000 0%, #ffffff 50%, #000000 100%)", "group": "DC Dark/Mystical"},
    "dr_manhattan": {"name": "Dr. Manhattan", "gradient": "linear-gradient(135deg, #1e90ff 0%, #87ceeb 50%, #1e90ff 100%)", "group": "DC Dark/Mystical"},

    # Cosmic & Special
    "phoenix_force": {"name": "Phoenix Force", "gradient": "linear-gradient(135deg, #cc3700 0%, #ff4500 100%)", "group": "Cosmic & Special"},
    "doctor_strange": {"name": "Doctor Strange", "gradient": "linear-gradient(135deg, #4b0082 0%, #8b008b 50%, #ff4500 100%)", "group": "Cosmic & Special"},
    "silver_surfer": {"name": "Silver Surfer", "gradient": "linear-gradient(135deg, #708090 0%, #c0c0c0 50%, #e8e8e8 100%)", "group": "Cosmic & Special"},
    "galactus_cosmic": {"name": "Galactus Cosmic", "gradient": "linear-gradient(135deg, #4b0082 0%, #9370db 50%, #4169e1 100%)", "group": "Cosmic & Special"},
    "iron_patriot": {"name": "Iron Patriot", "gradient": "linear-gradient(135deg, #dc143c 0%, #ffffff 33%, #0066cc 66%, #dc143c 100%)", "group": "Cosmic & Special"},
    "cosmic_entity": {"name": "Cosmic Entity", "gradient": "linear-gradient(135deg, #000000 0%, #4b0082 25%, #0066cc 50%, #9370db 75%, #000000 100%)", "group": "Cosmic & Special"},
    "infinity_stones": {"name": "Infinity Stones", "gradient": "linear-gradient(135deg, #9370db 0%, #0066cc 16%, #32cd32 33%, #ffd700 50%, #ff4500 66%, #dc143c 83%, #9370db 100%)", "group": "Cosmic & Special"},

    # DC Teams
    "justice_league": {"name": "Justice League", "gradient": "linear-gradient(135deg, #dc143c 0%, #0066cc 25%, #ffd700 50%, #32cd32 75%, #9370db 100%)", "group": "DC Teams"},
    "suicide_squad": {"name": "Suicide Squad", "gradient": "linear-gradient(135deg, #ff4500 0%, #000000 50%, #32cd32 100%)", "group": "DC Teams"},
    "birds_of_prey": {"name": "Birds of Prey", "gradient": "linear-gradient(135deg, #4b0082 0%, #000000 50%, #ffd700 100%)", "group": "DC Teams"},

    # Other
    "waverider_yellow": {"name": "Waverider Yellow", "gradient": "linear-gradient(135deg, #cc9900 0%, #ffd700 100%)", "group": "Other"},
    "atlantis_teal": {"name": "Atlantis Teal (Aquaman)", "gradient": "linear-gradient(135deg, #004d4d 0%, #008080 100%)", "group": "Other"},
    "gotham_night": {"name": "Gotham Night", "gradient": "linear-gradient(135deg, #000000 0%, #2d2d2d 100%)", "group": "Other"},
    "spawn": {"name": "Spawn", "gradient": "linear-gradient(135deg, #0a0a0a 0%, #8b0000 40%, #dc143c 70%, #32cd32 100%)", "group": "Other"},
}


# Static covers with labels
STATIC_COVERS = {
    "action-comics-1.webp": {"name": "Action Comics #1 (Superman)"},
    "amazing-fantasy-15.webp": {"name": "Amazing Fantasy #15 (Spider-Man)"},
    "detective-comics-27.webp": {"name": "Detective Comics #27 (Batman)"},
    "fantastic-four-1.webp": {"name": "Fantastic Four #1"},
    "incredible-hulk-1.webp": {"name": "Incredible Hulk #1"},
    "x-men-1.webp": {"name": "X-Men #1"},
    "avengers-1.webp": {"name": "Avengers #1"},
    "iron-man-1.webp": {"name": "Iron Man #1"},
    "amazing-spiderman-129.webp": { "name": "Amazing Spiderman #129" },
    "dark-knight-returns-1.webp": {"name": "Dark Knight Returns #1"},
    "watchmen-1.webp": {"name": "Watchmen #1"},
    "crisis-infinite-earths-1.webp": {"name": "Crisis on Infinite Earths #1"},
    "uncanny-x-men-141.webp": { "name": "Uncanny X-Men #141" },
    "spawn-1.webp": { "name": "Spawn #1" },
    "giant-size-x-men-1.webp": { "name": "Giant Size X-Men #1" },
    "avengers-4.webp": { "name": "Avengers #4 (Captain America)" },
    "amazing-spiderman-300.webp": { "name": "Amazing Spiderman #300" },
    "hulk-181.webp": { "name": "Hulk #181 (Wolverine)" },
    "infinity-gauntlet-1.webp": { "name": "Infinity Gauntlet #1" },
    "new-mutants-98.webp": { "name": "New Mutants #98 (Deadpool)" },
    "sandman-1.webp": { "name": "Sandman #1" },
    "mister-x-1.webp": { "name": "Mister X #1" },


    #"future.webp": { "name": "Future },

}