import unittest

from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

from app.main import Base, Product, Zone, seed


class SeedTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def count(self, model):
        return self.db.scalar(select(func.count()).select_from(model))

    def test_new_database_and_repeated_startup(self):
        seed(self.db)
        seed(self.db)
        self.assertEqual(self.count(Product), 2)
        self.assertEqual(self.count(Zone), 2)

    def test_empty_catalog_preserves_existing_zones(self):
        seed(self.db)
        self.db.execute(delete(Product))
        self.db.get(Zone, "centro").fee = 75
        self.db.commit()

        seed(self.db)

        self.assertEqual(self.count(Product), 0)
        self.assertEqual(self.count(Zone), 2)
        self.assertEqual(self.db.get(Zone, "centro").fee, 75)

    def test_partial_existing_zone_setup(self):
        self.db.add(Zone(id="centro", name="Centro personalizado", fee=20,
                         weekdays=[1], delivery_times=["09:00"], available=True))
        self.db.commit()

        seed(self.db)

        self.assertEqual(self.count(Product), 0)
        self.assertEqual(self.count(Zone), 1)
        self.assertEqual(self.db.get(Zone, "centro").name, "Centro personalizado")
