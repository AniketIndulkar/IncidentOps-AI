---
id: rb-notification-kafka
type: runbook
title: notification-worker cannot reach Kafka
service: notification-worker
---

# notification-worker cannot reach Kafka

notification-worker consumes `notifications.requests` and sends email, SMS and push messages. Use
this runbook when the worker logs `kafka.errors.NoBrokersAvailable` and message delivery stops.

## What NoBrokersAvailable means

The Kafka client could not open a connection to any broker in the bootstrap list. It does not mean
the cluster is down. It usually means the worker cannot resolve or reach the brokers, or that the
TLS handshake fails before the client sees any broker.

## Checks in order

1. From a worker pod run `nslookup kafka-bootstrap.messaging.svc` and `nc -zv kafka-bootstrap 9093`.
   If DNS fails, the problem is in the cluster network, not Kafka. Page the Platform team.
2. Check the broker TLS certificate expiry:
   `openssl s_client -connect kafka-bootstrap:9093 | openssl x509 -noout -enddate`.
   An expired broker certificate shows up in the client as NoBrokersAvailable, not as a TLS error.
3. Check the security group or network policy. A recent change to the `messaging` namespace network
   policy is the third most common cause.
4. Only then look at the brokers themselves: `kafka-broker-api-versions` against each broker.

## Recovery

- Once connectivity is back, the consumer group `notification-workers` resumes from the last
  committed offset. Do not reset offsets. Messages are not lost, only delayed.
- Messages older than 4 hours are no longer worth sending. The worker drops them and publishes them
  to the dead letter topic `notifications.dlq`. Support can replay them with
  `notifctl dlq replay --max-age 24h` if a business owner asks.
- Expect a burst of email and SMS when the backlog drains. The SMS provider is rate limited to
  50 messages per second, so a large backlog takes a while to clear.

## Escalation

Password reset emails travel through this worker. If the outage lasts more than 30 minutes, tell the
Support lead so they can warn customers who are locked out.
