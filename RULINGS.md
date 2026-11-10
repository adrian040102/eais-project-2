# Rulings for Project 2

**What the rulings cover.** You find what a company pays, when it pays, in which form and where you file in that company's own documents, and in the laws and cross-border conditions that apply to it. Below, we rule only on our own setup, i.e., our train data, the travelers' requests and the scoring. You can't answer these questions from a company's documents, so we fixed one answer for each.

**Numbers.** We kept the numbers of the earlier rulings, so some numbers are missing.

**Scope.** You find the trips, tickets and claims in scope in SCOPE.md.

## Dates, times and delays

**R1. Dates.** In each request, the traveler gives the day on which they write it. Words like "last Wednesday" count from that day, and all times are local times.

**R2. How we measure a delay.** A delay is the recorded time minus the planned time, in whole minutes, rounded down. For example, a train that arrives 59 minutes and 59 seconds late is 59 minutes late.

- **Planned times:** the times in our train data of the trains the traveler planned to take, with their planned connections. We use these times also when a company's documents refer to the times on its own website.
- **Recorded time:** the time of the train the traveler actually came in on.

**R3. Which data source counts.** When two data sources have a time for the same train and station, we use the source of the station's own country:

| Station | Source |
| --- | --- |
| Swiss | Swiss data |
| German | DB data |
| French | SNCF data |
| Italian | ViaggiaTreno data |

We use another source only when the source of the station's own country has no recorded time for the stop. The search tools already apply R3. In `departures` and `train_stops`, you find the source of each time in the field `source`.

**R4. Kinds of recorded time.** A recorded time counts whatever its kind, i.e., measured, recorded, announced or forecast. SNCF rounds most delays in its data to 5 minutes, and we don't correct for the rounding.

## Connections

**R6. Minimum change times.** You find the minimum change times in the table below, and at every other station the minimum change time is 5 minutes. A change between two stations of one city, e.g., between two stations in Paris, is part of the traveler's own plan.

| Station | Minutes |
| --- | --- |
| Bern, Lausanne, Brig, Lugano | 5 |
| Basel SBB, Zürich HB | 7 |
| Genève, Frankfurt(Main)Hbf, Strasbourg, Stuttgart Hbf | 8 |
| Milano Centrale, München Hbf, Lyon Part Dieu, Torino Porta Susa, Bologna Centrale, Firenze S. M. N. | 10 |
| Every Paris station, Roma Termini | 15 |

**R7. The train after a missed connection.** After a missed connection, or after a train that ends early or doesn't run, the traveler can tell you which train they took (topic `train_after_missed_connection`). If the traveler doesn't know, we count the first direct train to the destination that leaves at least the minimum change time after the late arrival. We never count a cancelled train.

## The traveler's facts

**R14. The cause of a delay.** When a company's rules depend on the cause of the delay, the cause is the reason the traveler reports (topic `delay_reason`).

**R16. The price.** The price is the amount the traveler says they paid for the ticket, in the currency they paid.

## Filing and scoring

**R11. One claim per company and ticket.** You file one claim with each company or office for each ticket, for the total amount you claim there.

**R12. Rounding of amounts.** We accept an amount within one cent of the amount under the company's rules.

**R21. Automatic payments.** Some companies pay some compensation automatically. In Project 2, you still file the claim on the company's form.

**R22. Deadlines and proof.** Every request is within every company's claim deadlines and waiting periods, and every traveler has the ticket and any proof of the delay that a company asks for. So you can leave deadlines and proof out of your decisions.

**R23. The train on the form.** On the form, the train is the first late or cancelled train that caused the late arrival. For a missed connection, the train is the late train you came in on, not the train you took afterwards.
