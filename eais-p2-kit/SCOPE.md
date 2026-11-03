# Project 2 scope: trips, tickets and rule documents

**The task.** Your assistant files delay claims for train trips in Switzerland, Germany, France and Italy, for single tickets, return tickets and through tickets. You find each company's rules in its own documents, and you find its claim form with `list_forms()` and `get_form(company)`.

**Companies.** The kit has the documents and claim forms of six companies, i.e., SBB, DB, SNCF, OUIGO, Trenitalia and Trenord, and the laws and cross-border conditions that apply to them. It also has the documents of TGV Lyria and TILO, which have no claim forms of their own. In the final, some trips are on trains of other companies, and you get the documents and claim forms of those companies only through the tools, during the run.

**Our rules.** You find the rules for our train data, the travelers' requests and the scoring in RULINGS.md.

## Tickets in scope

- **Single tickets,** including saver fares, e.g., Sparbillett, Super Sparpreis and the OUIGO fares.
- **Tickets with a discount card,** e.g., the Half Fare travelcard, BahnCard 25 or 50, or Carte Avantage. The discount card itself is out of scope.
- **Return tickets.**
- **Through tickets,** i.e., several trains bought in one booking, with one payment, from one company. Tickets from different bookings or different companies are separate tickets.

## Out of scope

We left the following out of the requests, because our train data doesn't cover the trains, or because the claim depends on facts that we can't put into a request in a fair way.

- **Ticket types**
  - Passes and travelcards with a value per day or per month, e.g., GA, BahnCard 100, Deutschland-Ticket, Länder-Tickets, Interrail, Eurail, Swiss Travel Pass and season tickets.
  - Zone tickets of tariff networks, e.g., ZVV, Arcobaleno and STIBM, because a zone ticket has no destination station.
  - Night trains, e.g., Nightjet, Intercités de nuit and Intercity Notte, because their prices include sleeper and couchette supplements.
- **Trains and sellers**
  - Trains of Italo, ÖBB, FlixTrain and Trenitalia France. For Italo, we record no train times at all.
  - Tickets from resellers and travel agencies, e.g., Trainline or Omio.
  - Bookings that combine trains of different companies in one payment, other than TGV Lyria and the joint DB and SNCF trains between France and Germany.
- **Other claims,** e.g., hotels, taxis, meals, new tickets, refunds for trips the traveler didn't make, and claims for seat reservations, bicycles, dogs or groups.

## Rule documents

**What's in the kit.** You find 45 official documents of the kit companies, and of the laws that apply to them, in `rules/`, in German, French, Italian and English, with the conditions of carriage in full.

**Our copies.** Our copies keep every word of the rules. We removed menus, footers and images, and we left the fare tables out of the Trenitalia price list.

**Size.** The documents add up to about 660,000 tokens, so they don't fit into one prompt of 128,000 tokens, and you have to search them.

## Train data

Our train data holds recorded times for Switzerland, Germany, France and Italy. The kit that you download from the Project 2 page of the course app has our train data for the practice days, 19 to 26 October, for all four countries. You search it with the train tools. When two sources have a time for the same stop, you find the source that counts in ruling R3.
