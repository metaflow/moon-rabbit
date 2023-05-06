#!/bin/bash
#
# Backup a Postgresql database into a daily file.
#

BACKUP_DIR=/mnt/backup
DAYS_TO_KEEP=14
FILE_SUFFIX=.sql.gz
DATABASE=rabbit
USER=postgres

FILE=${DATABASE}`date +"%Y%m%d%H%M%S"`${FILE_SUFFIX}

OUTPUT_FILE=${BACKUP_DIR}/${FILE}

# do the database backup (dump)
# use this command for a database server on localhost. add other options if need be.
sudo -u $USER pg_dump ${DATABASE} --no-owner --no-privilege --no-acl --column-inserts | gzip > ${OUTPUT_FILE}

# show the user the result
echo "${OUTPUT_FILE} was created:"
ls -l ${OUTPUT_FILE}

# prune old backups
find $BACKUP_DIR -maxdepth 1 -mtime +$DAYS_TO_KEEP -name "*${FILE_SUFFIX}" -exec rm -rf '{}' ';'
