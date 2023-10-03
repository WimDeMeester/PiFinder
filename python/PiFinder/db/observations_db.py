import json
from pathlib import Path
from typing import Tuple
from sqlite3 import Connection, Cursor, Error
from PiFinder.db.db import Database
import PiFinder.utils as utils
from PiFinder.composite_object import CompositeObject


class ObservationsDatabase(Database):
    def __init__(self, db_path: Path = utils.observations_db):
        new_db = False
        if not db_path.exists():
            new_db = True
        conn, cursor = self.get_database(db_path)
        super().__init__(conn, cursor, db_path)
        if new_db:
            self.create_tables()

        self.observed_objects_cache = None

    def create_tables(self, force_delete: bool = False):
        """
        Creates the base logging tables
        """

        # initialize tables
        self.cursor.execute(
            """
               CREATE TABLE obs_sessions(
                    id INTEGER PRIMARY KEY,
                    start_time_local INTEGER,
                    lat NUMERIC,
                    lon NUMERIC,
                    timezone TEXT,
                    UID TEXT
               )
            """
        )

        self.cursor.execute(
            """
               CREATE TABLE obs_objects(
                    id INTEGER PRIMARY KEY,
                    session_uid TEXT,
                    obs_time_local INTEGER,
                    catalog TEXT,
                    sequence INTEGER,
                    solution TEXT,
                    notes TEXT
               )
            """
        )
        self.conn.commit()

    def get_observations_database(self) -> Tuple[Connection, Cursor]:
        return self.get_database(utils.observations_db)

    def create_obs_session(self, start_time, lat, lon, timezone, uuid):
        q = """
            INSERT INTO obs_sessions(
                start_time_local,
                lat,
                lon,
                timezone,
                uid
            )
            VALUES
            (
                :start_time,
                :lat,
                :lon,
                :timezone,
                :uuid
            )
        """

        self.cursor.execute(
            q,
            {
                "start_time": start_time,
                "lat": lat,
                "lon": lon,
                "timezone": timezone,
                "uuid": uuid,
            },
        )
        self.conn.commit()

    def log_object(self, session_uuid, obs_time, catalog, sequence, solution, notes):
        q = """
            INSERT INTO obs_objects(
                session_uid,
                obs_time_local,
                catalog,
                sequence,
                solution,
                notes
            )
            VALUES
            (
                :session_uuid,
                :obs_time,
                :catalog,
                :sequence,
                :solution,
                :notes
            )
        """

        self.cursor.execute(
            q,
            {
                "session_uuid": session_uuid,
                "obs_time": obs_time,
                "catalog": catalog,
                "sequence": sequence,
                "solution": json.dumps(solution),
                "notes": json.dumps(notes),
            },
        )
        self.conn.commit()

        observation_id = self.cursor.execute(
            "select last_insert_rowid() as id"
        ).fetchone()["id"]
        return observation_id

    def get_observed_objects(self):
        """
        Returns a list of all observed objects
        """
        logs = self.cursor.execute(
            f"""
                select distinct catalog, sequence from obs_objects
            """
        ).fetchall()

        return logs

    def load_observed_objects_cache(self):
        """
        (re)Loads the logged object cache
        """
        self.observed_objects_cache = [
            (x["catalog"], x["sequence"]) for x in self.get_observed_objects()
        ]

    def check_logged(self, obj_record: CompositeObject):
        """
        Returns true/false if this object has been observed
        """
        # safety check
        if self.observed_objects_cache == None:
            self.load_observed_objects_cache()

        if (
            obj_record.catalog_code,
            obj_record.sequence,
        ) in self.observed_objects_cache:
            return True

        return False

    def get_logs_for_object(self, obj_record: CompositeObject):
        """
        Returns a list of observations for a particular object
        """
        logs = self.cursor.execute(
            f"""
                select * from obs_objects
                where catalog = :catalog
                and sequence = :sequence
            """,
            {"catalog": obj_record.catalog_code, "sequence": obj_record.sequence},
        ).fetchall()

        return logs

    def close(self):
        self.conn.close()