from db import init_db, get_or_create_topic, add_card, get_due_cards, update_card_after_review, get_topic_mastery

init_db()
print("Database initialized")

topic_id = get_or_create_topic("Photosynthesis", "test notes")
print(f"Topic created: id={topic_id}")

card_id = add_card(topic_id, "Why do plants need sunlight?",
                    {"A": "Energy", "B": "Color", "C": "Smell", "D": "Weight"}, "A")
print(f"Card created: id={card_id}")

due = get_due_cards()
print(f"Due cards: {len(due)}")
print(due)

update_card_after_review(card_id, quality=4)
print("Updated after review")

mastery = get_topic_mastery()
print("Mastery report:", mastery)
