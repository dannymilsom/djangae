import datetime
import logging

from google.appengine.ext import db
from google.appengine.api.datastore import Key, Delete
from google.appengine.datastore.datastore_rpc import TransactionOptions

from .unique_utils import unique_identifiers_from_entity
from .utils import key_exists
from .exceptions import IntegrityError

DJANGAE_LOG = logging.getLogger("djangae")

class UniqueMarker(db.Model):
    instance = db.StringProperty()
    created = db.DateTimeProperty(required=True, auto_now_add=True)

    @staticmethod
    def kind():
        return "__unique_marker"

def acquire_identifiers(identifiers, entity_key):
    @db.transactional(propagation=TransactionOptions.INDEPENDENT, xg=True)
    def acquire_marker(identifier):
        identifier_key = Key.from_path(UniqueMarker.kind(), identifier)


        marker = UniqueMarker.get(identifier_key)
        if marker:
            #If the marker instance is None, and the marker is older then 5 seconds then we wipe it out
            #and assume that it's stale.
            if not marker.instance and (datetime.datetime.utcnow() - marker.created).seconds > 5:
                marker.delete()
            elif marker.instance and Key(marker.instance) != entity_key and key_exists(Key(marker.instance)):
                raise IntegrityError()
            else:
                #The marker is ours anyway
                return marker

        marker = UniqueMarker(
            key=identifier_key,
            instance=str(entity_key) if entity_key.id_or_name() else None, #May be None if unsaved
            created=datetime.datetime.utcnow()
        )
        marker.put()
        return marker

    markers = []
    try:
        for identifier in identifiers:
            markers.append(acquire_marker(identifier))
            DJANGAE_LOG.debug("Acquired unique marker for %s", identifier)
    except:
        for marker in markers:
            marker.delete()
        DJANGAE_LOG.debug("Due to an error, deleted markers %s", markers)
        raise
    return markers


def update_markers(model, old_entity, new_entity):
    """
        Given an old entity state, and the new state, updates the identifiers
        appropriately. Should be called before saving the new_state
    """
    old_ids = set(unique_identifiers_from_entity(model, old_entity, ignore_pk=True))
    new_ids = set(unique_identifiers_from_entity(model, new_entity, ignore_pk=True))

    to_release = old_ids - new_ids
    to_acquire = new_ids - old_ids

    #Acquire first, because if that fails then we don't want to alter what's already there
    new_markers = acquire_identifiers(to_acquire, new_entity.key())

    #Now we release the ones we don't want anymore
    release_identifiers(to_release)

    return new_markers

def update_instance_on_markers(entity, markers):

    @db.transactional(propagation=TransactionOptions.INDEPENDENT)
    def update(marker, instance):
        marker = UniqueMarker.get(marker.key())
        if not marker:
            return

        marker.instance = instance
        marker.put()

    instance = str(entity.key())
    for marker in markers:
        update(marker, instance)

def acquire_bulk(model, entities):
    markers = []
    try:
        for entity in entities:
            markers.append(acquire(model, entity))

    except:
        for m in markers:
            Delete([ x.key() for x in m])
        raise
    return markers

def acquire(model, entity):
    """
        Given a model and entity, this tries to acquire unique marker locks for the instance. If the locks already exist
        then an IntegrityError will be thrown.
    """

    identifiers = unique_identifiers_from_entity(model, entity, ignore_pk=True)
    return acquire_identifiers(identifiers, entity.key())

def release_identifiers(identifiers):

    @db.non_transactional
    def delete():
        keys = [ Key.from_path(UniqueMarker.kind(), x) for x in identifiers ]
        Delete(keys)

    delete()
    DJANGAE_LOG.debug("Deleted markers with identifiers: %s", identifiers)

def release(model, entity):
    identifiers = unique_identifiers_from_entity(model, entity, ignore_pk=True)
    release_identifiers(identifiers)
