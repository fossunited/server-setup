## Server Details 

- Running on a 4GB Ram Linode
- 80GB Storage
- Files included under `/home/mangesh`
- Webserver: Caddy (inside docker container), path: `/home/mangesh/caddy`
- Everyone with a fossunited.org domain can register an account, but for additional security admins on the Nextcloud Interface have to approve the creation of that nextcloud account 

## Instructions and Guidelines

This document consists of some document and information about how nextcloud-aio was selfhosted. Includes all the information from scratch.

The caddy and nextcloud-aio docker related files can be found in the same directory on this repo, mostly includes default configuration.  

[Nextcloud All in One](https://github.com/nextcloud/all-in-one) is the official nextcloud installation guide which includes installation of nextcloud with almost every basic feature including files and some apps installed by default in it. All the components in AIO are running inside one separate container. All of nextcloud can be killed if the container `nextcloud-aio-mastercontainer` is killed. 

To get a list of all the docker containers related to nextcloud, please login to server and execute the following command:

- `sudo docker ps`

### To update nextcloud

Make sure to update nextcloud after checking [Changelogs](https://github.com/nextcloud/all-in-one/releases) and if it feels very much needed to update. 

Current version of nextcloud is: 29.0.4, can be found via `sudo docker inspect nextcloud-aio-nextcloud | grep NEXTCLOUD_VERSION`

#### The update procedure is as follows: 

- Open the following url in your browser: https://<nextcloud-server-ip>/containers, the browser will ask you to kill the nextcloud-aio-apache container. 
- SSH into the server and kill the `nextcloud-aio-apache` container by running `sudo docker stop nextcloud-aio-apache`. 
- After this, reload the browser and you should be able to see the AIO Container Dashboard. Finally, update the mastercontainer :). 

(there might be some changes in the above process, please read docs if there's any change.)

## Password Policy 

Please take a note that the passwords will be expired in every 365 days, after 3 wrong login attempts the account will be locked until any manual action is taken by any of the super admins. Configure this at [https://nextcloud.fossunited.org/settings/admin/security](https://nextcloud.fossunited.org/settings/admin/security)

**Note: If this document doesn't help you anyway, please head over to: https://github.com/nextcloud/all-in-one?tab=readme-ov-file#how-to-update-the-containers.**
