from common import database, get_object_store
from hashlib import sha256
import logging
import os
import re
import requests
import tempfile


__all__ =['get_experiment_from_provider']


def get_experiment_from_provider(session, remote_addr,
                                 provider, provider_path):
    try:
        getter = _PROVIDERS[provider]
    except KeyError:
        raise KeyError("No such provider %s" % provider)
    return getter(session, remote_addr, provider, provider_path)


def _get_from_link(session, remote_addr, provider, provider_path,
                   link, filename, filehash=None):
    # Check for existence of experiment
    experiment = session.query(database.Experiment).get(filehash)
    if experiment:
        logging.info("Experiment with hash exists, no need to download")
    else:
        logging.info("Downloading %s", link)
        fd, local_path = tempfile.mkstemp(prefix='provider_download_')
        try:
            # Download file & hash it
            response = requests.get(link, stream=True)
            response.raise_for_status()
            hasher = sha256()
            with open(local_path, 'wb') as f:
                for chunk in response.iter_content(4096):
                    f.write(chunk)
                    hasher.update(chunk)

            filehash = hasher.hexdigest()

            # Check for existence of experiment
            experiment = session.query(database.Experiment).get(filehash)
            if experiment:
                logging.info("File exists")
            else:
                # Insert it on S3
                object_store = get_object_store()
                object_store.upload_file('experiments', filehash,
                                         local_path)
                logging.info("Inserted file in storage")

                # Insert it in database
                experiment = database.Experiment(hash=filehash)
                session.add(experiment)
        finally:
            os.close(fd)
            os.remove(local_path)

    # Insert Upload in database
    upload = database.Upload(experiment=experiment,
                             filename=filename,
                             submitted_ip=remote_addr,
                             provider_key='%s/%s' % (provider, provider_path))
    session.add(upload)
    session.commit()

    return upload


# Providers


_osf_path = re.compile('^[a-zA-Z0-9]+$')


def _osf(session, remote_addr, provider, path):
    if _osf_path.match(path) is None:
        return None
    logging.info("Querying OSF for '%s'", path)
    req = requests.get('https://api.osf.io/v2/files/{0}/'.format(path),
                       headers={'Content-Type': 'application/json',
                                'Accept': 'application/json'})
    if req.status_code != 200:
        logging.info("Got error %s", req.status_code)
        return None
    response = req.json()
    try:
        link = response['data']['links']['download']
    except KeyError:
        return None
    except ValueError:
        logging.error("Got invalid JSON from osf.io")
    else:
        try:
            filehash = response['data']['attributes']['extra']['hashes']['sha256']
        except KeyError:
            filehash = None
        try:
            filename = response['data']['attributes']['name']
        except KeyError:
            filename = 'unnamed_osf_file'
        logging.info("Got response: %s %s %s", link, filehash, filename)
        return _get_from_link(session, remote_addr, provider, path,
                              link, filename, filehash)


def _figshare(session, remote_addr, provider, path):
    raise NotImplementedError  # TODO


_PROVIDERS = {
    'osf.io': _osf,
    'figshare.com': _figshare,
}
