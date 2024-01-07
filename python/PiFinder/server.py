import time
import logging
import io
import datetime
import uuid
import json

from bottle import (
    Bottle,
    run,
    request,
    template,
    response,
    static_file,
    debug,
    redirect,
    CherootServer,
)

from PIL import Image

from PiFinder.keyboard_interface import KeyboardInterface
from PiFinder import sys_utils, utils, calc_utils
from PiFinder.db.observations_db import (
    ObservationsDatabase,
)

# Generate a secret to validate the auth cookie
SESSION_SECRET = str(uuid.uuid4())


def auth_required(func):
    def auth_wrapper(*args, **kwargs):
        # check for and validate cookie
        auth_cookie = request.get_cookie("pf_auth", secret=SESSION_SECRET)
        if auth_cookie:
            return func(*args, **kwargs)

        return template("login", origin_url=request.url)

    return auth_wrapper


class Server:
    def __init__(self, q, gps_queue, shared_state):
        self.version_txt = f"{utils.pifinder_dir}/version.txt"
        self.q = q
        self.gps_queue = gps_queue
        self.shared_state = shared_state
        self.ki = KeyboardInterface()

        button_dict = {
            "UP": self.ki.UP,
            "DN": self.ki.DN,
            "ENT": self.ki.ENT,
            "A": self.ki.A,
            "B": self.ki.B,
            "C": self.ki.C,
            "D": self.ki.D,
            "ALT_UP": self.ki.ALT_UP,
            "ALT_DN": self.ki.ALT_DN,
            "ALT_A": self.ki.ALT_A,
            "ALT_B": self.ki.ALT_B,
            "ALT_C": self.ki.ALT_C,
            "ALT_D": self.ki.ALT_D,
            "ALT_0": self.ki.ALT_0,
            "LNG_A": self.ki.LNG_A,
            "LNG_B": self.ki.LNG_B,
            "LNG_C": self.ki.LNG_C,
            "LNG_D": self.ki.LNG_D,
            "LNG_ENT": self.ki.LNG_ENT,
        }

        self.system = sys_utils.System()

        app = Bottle()
        debug(True)

        @app.route("/images/<filename:re:.*\.png>")
        def send_image(filename):
            return static_file(filename, root="views/images", mimetype="image/png")

        @app.route("/js/<filename>")
        def send_static(filename):
            return static_file(filename, root="views/js")

        @app.route("/css/<filename>")
        def send_static(filename):
            return static_file(filename, root="views/css")

        @app.route("/")
        def home():
            # need to collect alittle status info here
            with open(self.version_txt, "r") as ver_f:
                software_version = ver_f.read()

            location = self.shared_state.location()

            lat_text = ""
            lon_text = ""
            gps_icon = "gps_off"
            gps_text = "Not Locked"
            if location["gps_lock"] == True:
                gps_icon = "gps_fixed"
                gps_text = "Locked"
                lat_text = str(location["lat"])
                lon_text = str(location["lon"])

            ra_text = "0"
            dec_text = "0"
            camera_icon = "broken_image"
            if self.shared_state.solve_state() == True:
                camera_icon = "camera_alt"
                solution = self.shared_state.solution()
                hh, mm, _ = calc_utils.ra_to_hms(solution["RA"])
                ra_text = f"{hh:02.0f}h{mm:02.0f}m"
                dec_text = f"{solution['Dec']: .2f}"

            return template(
                "index",
                software_version=software_version,
                wifi_mode=self.system.get_wifi_mode(),
                ip=self.system.get_local_ip(),
                network_name=self.system.get_connected_ssid(),
                gps_icon=gps_icon,
                gps_text=gps_text,
                lat_text=lat_text,
                lon_text=lon_text,
                camera_icon=camera_icon,
                ra_text=ra_text,
                dec_text=dec_text,
            )

        @app.route("/login", method="post")
        def login():
            password = request.forms.get("password")
            origin_url = request.forms.get("origin_url", "/")
            if sys_utils.verify_password("pifinder", password):
                # set auth cookie, doesnt matter whats in it, just as long
                # as it's there and cryptographically valid
                response.set_cookie("pf_auth", str(uuid.uuid4()), secret=SESSION_SECRET)
                redirect(origin_url)
            else:
                return template(
                    "login", origin_url=origin_url, error_message="Invalid Password"
                )

        @app.route("/remote")
        @auth_required
        def remote():
            return template(
                "remote",
            )

        @app.route("/network")
        @auth_required
        def network_page():
            show_new_form = request.query.add_new or 0

            return template(
                "network",
                system=self.system,
                show_new_form=show_new_form,
            )

        @app.route("/network/add", method="post")
        @auth_required
        def network_update():
            ssid = request.forms.get("ssid")
            psk = request.forms.get("psk")
            if len(psk) < 8:
                key_mgmt = "NONE"
            else:
                key_mgmt = "WPA-PSK"

            self.system.add_wifi_network(ssid, key_mgmt, psk)
            return network_page()

        @app.route("/network/delete/<network_id:int>")
        @auth_required
        def network_delete(network_id):
            self.system.delete_wifi_network(network_id)
            return network_page()

        @app.route("/network/update", method="post")
        @auth_required
        def network_update():
            wifi_mode = request.forms.get("wifi_mode")
            ap_name = request.forms.get("ap_name")
            host_name = request.forms.get("host_name")

            self.system.set_wifi_mode(wifi_mode)
            self.system.set_ap_name(ap_name)
            self.system.set_host_name(host_name)
            return template("restart")

        @app.route("/tools/pwchange", method="post")
        @auth_required
        def password_change():
            current_password = request.forms.get("current_password")
            new_passworda = request.forms.get("new_passworda")
            new_passwordb = request.forms.get("new_passwordb")

            if new_passworda == "" or current_password == "" or new_passwordb == "":
                return template(
                    "tools", error_message="You must fill in all password fields"
                )

            if new_passworda == new_passwordb:
                if sys_utils.change_password(
                    "pifinder", current_password, new_passworda
                ):
                    return template("tools", status_message="Password Changed")
                else:
                    return template("tools", error_message="Incorrect current password")
            else:
                return template("tools", error_message="New passwords do not match")

        @app.route("/system/restart")
        @auth_required
        def system_restart():
            """
            Restarts the RPI system
            """

            sys_utils.restart_system()
            return "restarting"

        @app.route("/system/restart_pifinder")
        @auth_required
        def system_restart():
            """
            Restarts just the PiFinder software
            """
            sys_utils.restart_pifinder()
            return "restarting"

        @app.route("/observations")
        @auth_required
        def obs_sessions():
            obs_db = ObservationsDatabase()
            if request.query.get("download", 0) == "1":
                # Download all as TSV
                observations = obs_db.observations_as_tsv()

                response.set_header(
                    "Content-Disposition", "attachment; filename=observations.tsv"
                )
                response.set_header("Content-Type", "text/tsv")
                return observations

            # regular html page of sessions
            sessions = obs_db.get_sessions()
            metadata = {
                "sess_count": len(sessions),
                "object_count": sum(x["observations"] for x in sessions),
                "total_duration": sum(x["duration"] for x in sessions),
            }
            return template("obs_sessions", sessions=sessions, metadata=metadata)

        @app.route("/observations/<session_id>")
        @auth_required
        def obs_session(session_id):
            obs_db = ObservationsDatabase()
            if request.query.get("download", 0) == "1":
                # Download all as TSV
                observations = obs_db.observations_as_tsv(session_id)

                response.set_header(
                    "Content-Disposition",
                    f"attachment; filename=observations_{session_id}.tsv",
                )
                response.set_header("Content-Type", "text/tsv")
                return observations

            session = obs_db.get_sessions(session_id)[0]
            objects = obs_db.get_logs_by_session(session_id)
            ret_objects = []
            for obj in objects:
                obj_ = dict(obj)
                obj_notes = json.loads(obj_["notes"])
                obj_["notes"] = "<br>".join(
                    [f"{key}: {value}" for key, value in obj_notes.items()]
                )
                ret_objects.append(obj_)
            return template("obs_session_log", session=session, objects=ret_objects)

        @app.route("/tools")
        @auth_required
        def tools():
            return template("tools")

        @app.route("/tools/backup")
        @auth_required
        def tools_backup():
            backup_file = sys_utils.backup_userdata()

            # Assumes the standard backup location
            return static_file("PiFinder_backup.zip", "/home/pifinder/PiFinder_data")

        @app.route("/tools/restore", method="post")
        @auth_required
        def tools_backup():
            sys_utils.remove_backup()
            backup_file = request.files.get("backup_file")
            backup_file.filename = "PiFinder_backup.zip"
            backup_file.save("/home/pifinder/PiFinder_data")

            sys_utils.restore_userdata(
                "/home/pifinder/PiFinder_data/PiFinder_backup.zip"
            )

            return template("restart_pifinder")

        @app.route("/key_callback", method="POST")
        @auth_required
        def key_callback():
            button = request.json.get("button")
            if button in button_dict:
                self.key_callback(button_dict[button])
            else:
                self.key_callback(int(button))
            return {"message": "success"}

        @app.route("/image")
        def serve_pil_image():
            empty_img = Image.new(
                "RGB", (60, 30), color=(73, 109, 137)
            )  # create an image using PIL
            img = None
            try:
                img = self.shared_state.screen()
            except (BrokenPipeError, EOFError):
                pass
            response.content_type = "image/png"  # adjust for your image format

            if img is None:
                img = empty_img
            img_byte_arr = io.BytesIO()
            img.save(img_byte_arr, format="PNG")  # adjust for your image format
            img_byte_arr = img_byte_arr.getvalue()

            return img_byte_arr

        @app.route("/gps-lock")
        @auth_required
        def gps_lock():
            msg = (
                "fix",
                {
                    "lat": 50,
                    "lon": 3,
                    "altitude": 10,
                },
            )
            self.gps_queue.put(msg)

        @app.route("/time-lock")
        @auth_required
        def time_lock():
            msg = ("time", datetime.datetime.now())
            self.gps_queue.put(msg)

        # If the PiFinder software is running as a service
        # it can grab port 80.  If not, it needs to use 8080
        try:
            run(
                app,
                host="0.0.0.0",
                port=80,
                quiet=True,
                debug=True,
                server=CherootServer,
            )
        except (PermissionError, OSError):
            logging.info("Web Interface on port 8080")
            run(
                app,
                host="0.0.0.0",
                port=8080,
                quiet=True,
                debug=True,
                server=CherootServer,
            )

    def key_callback(self, key):
        self.q.put(key)


def run_server(q, gps_q, shared_state):
    Server(q, gps_q, shared_state)
