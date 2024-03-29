#Follows model set in 
#https://github.com/hcarter333/datasette-enrichments-gmap-geocode/blob/main/datasette_enrichments_gmap_geocode/__init__.py
#original version is copied from the above link
from datasette import hookimpl
from datasette_enrichments import Enrichment
from datasette.database import Database
from wtforms import (
    Form,
    StringField,
    TextAreaField,
    PasswordField,
)
from wtforms.validators import DataRequired
import httpx
import json
import secrets
import sqlite_utils


@hookimpl
def register_enrichments(datasette):
    return [QRZEnrichment()]


class QRZEnrichment(Enrichment):
    name = "QRZ callsign info"
    slug = "qrz_call_info"
    #use https://github.com/hcarter333/rm-rbn-history/blob/e966e5f445cf2ad6a62ee584c4a9b052128d3507/auto_geo_update.py
    #as an example
    #for a full list of fields see https://www.qrz.com/page/current_spec.html
    description = "Gather qrz.com information for the callsign"
    batch_size = 1
    log_traceback = True

    async def get_config_form(self, datasette: "Datasette", db: Database, table: str):
        def get_text_columns(conn):
            db = sqlite_utils.Database(conn)
            return [
                key for key, value in db[table].columns_dict.items() if value == str
            ]

        text_columns = await db.execute_fn(get_text_columns)

        class ConfigForm(Form):
            input = TextAreaField(
                "Callsign input",
                description="A template to run against each row to generate callsign addresses. Use {{ COL }} for callsign column.",
                validators=[DataRequired(message="Prompt is required.")],
                default=" ".join(["{{ %s }}" % c for c in text_columns]),
            )
            json_column = StringField(
                "Store JSON in column",
                description="To store full JSON from Google Maps API, enter a column name here",
                render_kw={
                    "placeholder": "Leave this blank if you only want to store latitude/longitude"
                },
            )

        def stash_api_key(form, field):
            if not hasattr(datasette, "_enrichments_gmap_geocode_stashed_keys"):
                datasette._enrichments_qrz_stashed_keys = {}
            key = secrets.token_urlsafe(16)
            datasette._enrichments_qrz_stashed_keys[key] = field.data
            field.data = key

        class ConfigFormWithKey(ConfigForm):
            api_key = PasswordField(
                "API key",
                description="Your QRZ API key",
                validators=[
                    DataRequired(message="API key is required."),
                    stash_api_key,
                ],
            )

        plugin_config = datasette.plugin_config("datasette-enrichments-qrz") or {}
        api_key = plugin_config.get("api_key")

        return ConfigForm if api_key else ConfigFormWithKey

    qrz_sess = "none"
    async def enrich_batch(self, rows, datasette, db, table, pks, config):
        global qrz_sess
        if(qrz_sess == "none"):
            #  https://maps.googleapis.com/maps/api/geocode/json?address=URI-ENCODED-PLACENAME&key=b591350c2f9c48a7b7176660bbfd802a
            #url = "https://maps.googleapis.com/maps/api/geocode/json"
            #QRZ takes a bit more effort to access
            qrz_pswd = os.getenv("QRZ_PSWD")
            qrz_pswd = qrz_pswd.replace('"','')
            request_string = 'https://xmldata.qrz.com/xml/?username='+username+';password='+qrz_pswd
            sess = requests.get('https://xmldata.qrz.com/xml/?username='+username+';password='+qrz_pswd)
            root = ET.fromstring(sess.text)
            sess_id = root.find('{http://xmldata.qrz.com}Session/{http://xmldata.qrz.com}Key')
            auto_geo_vars.qrz_sess = sess_id
        else:
            sess_id = auto_geo_vars.qrz_sess
        
        params = {
            "key": resolve_api_key(datasette, config),
            "limit": 1,
        }
        json_column = config.get("json_column")
        if not json_column:
            params["no_annotations"] = 1
        row = rows[0]
        input = config["input"]
        for key, value in row.items():
            input = input.replace("{{ %s }}" % key, str(value or "")).replace(
                "{{%s}}" % key, str(value or "")
            )
#    r = requests.get('https://xmldata.qrz.com/xml/current/?s='+sess_id.text+';callsign='+callsign)
#    root = ET.fromstring(r.text)
    #print(r.text)
#    #now, get addr1, addr2, and state
#    addr1 = root.find('{http://xmldata.qrz.com}Callsign/{http://xmldata.qrz.com}addr1')
#    addr2 = root.find('{http://xmldata.qrz.com}Callsign/{http://xmldata.qrz.com}addr2')
#    state = root.find('{http://xmldata.qrz.com}Callsign/{http://xmldata.qrz.com}state')
#    country = root.find('{http://xmldata.qrz.com}Callsign/{http://xmldata.qrz.com}country')

        params["address"] = input
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        if not data["results"]:
            raise ValueError("No results found for {}".format(input))
        result = data["results"][0]
        update = {
            "latitude": result["geometry"]["location"]["lat"],
            "longitude": result["geometry"]["location"]["lng"],
        }
        if json_column:
            update[json_column] = json.dumps(data)

        ids = [row[pk] for pk in pks]

        def do_update(conn):
            sqlite_utils.Database(conn)[table].update(ids, update, alter=True)

        await db.execute_write_fn(do_update)


class ApiKeyError(Exception):
    pass


def resolve_api_key(datasette, config):
    plugin_config = datasette.plugin_config("datasette-enrichments-gmap-geocode") or {}
    api_key = plugin_config.get("api_key")
    if api_key:
        return api_key
    # Look for it in config
    api_key_name = config.get("api_key")
    if not api_key_name:
        raise ApiKeyError("No API key reference found in config")
    # Look it up in the stash
    #                          datasette_enrichments_gmaps_api_stashed_keys
    if not hasattr(datasette, "_enrichments_gmap_geocode_stashed_keys"):
        raise ApiKeyError("No API key stash found")
    stashed_keys = datasette._enrichments_gmap_geocode_stashed_keys
    if api_key_name not in stashed_keys:
        raise ApiKeyError("No API key found in stash for {}".format(api_key_name))
    return stashed_keys[api_key_name]
