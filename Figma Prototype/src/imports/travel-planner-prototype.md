I want to create a prototype for a travel planner that would help guide the traveler from initial inspiration (I want to go someplace warm) to the final itinerary planning in a multi-step process. It will:

Understand the user's needs and goals in each step.

Roughly the stages are :

0. Criteria: Dates (general or specific), who is travelling, any preference on destination, any preference on type of activity, budget, travel time/ distance etc. .. all expressed in natural language by the user.
1. Inspiration: Where to go: Selecting destination. For each suggestion, give pros and cons based on the above step. Highlight the best recommendation. Give estimate of travel time and cost and why that is the best recommendation
2. Forming an itinerary: How to plan the entire trip, when to fly, different places to stay. Rough dates, travel mode - flight vs. drive vs. cruise etc. Trade offs and estimates.
3. Finalizing the bookings based on Itinerary: Which airlines, which hotels. Specific dates
4. Decide on activities: What are the activities that needs to be booked, where and when.

Consider all the factors for decision making:

> Explicit factors specified by user
> Implicit or common sense factors not explicitly specified by the user
> Any constrains or factors implied by the previous steps
> Any preference/ memory that is derived from history and user's profile/ past interactions (it can be a placeholder for now)

Ask the user if any critical piece of data is missing before making big assumptions.
Provide top options to choose from. The user can ask for more options or change some of the factors and go back.
Once the user makes the selection, it moves to the next stage of planning.
User can always back-track and explore an alternative path.


The interaction mode of this application would be hybrid. The user would be able to express her thoughts and ideas in free-form natural language. The app would take that input and interpret them as the context in which it can suggest destinations. The app would also take that same input and create a traveler profile, representing key things like who are travelling, budget, type of activities, pets, special requirement etc.  

Travel planning is inspiration based and visual. Each destination idea would be presented as idea boards: with hero images of the destination, a short "selling point" about that vacation, some key considerations like a weather card, highlights of activities, highlights from reviews etc.

The user should be able to interact with those idea boards, expand/ collapse them, delete them, ask to generate more ideas.

Overall, the app, while providing these traditional ways of interaction, would also have an intelligent, always-on advisor, which would keep giving the user context aware guidance. for example, at this stage it can give the user some guidance/ recommendation about how to choose between the destination ideas, it can call out any conflict or red flags as the user builds in the itinerary or suggest the next set of actions to the user. moreover, there would be an interface where the user can "talk to" the app instead of clicking on buttons.

Once the user short-lists a few destination boards, she can then start building up the itinerary. The app can suggest how to break down the trip into multi-day plans with activities planned for each day. It should consider the fly in, check in, travel between cities and fly out plan. If the user changes anything in the travel profile part, these itinerary would either automatically update to accommodate or the adviser would pop in and suggest refreshing the itinerary that clashes with the changes. you decide which would be better. 

once the itineraries are built for a few, those would help the user to further short list the options. 

remaining options, then the planning advisor can help further build the next steps where it can select options to stay (hotels or Vacation rental options), flights the big ticket items. it would suggest the top recommendation but would allow the user to see alternatives or change the criteria and regenerate recommendations. 

Also, the user can see the top options for activities, the user, once choosing the activities, can also revisit the lodging options, if needed, based on the proximities to the activities. 

Finally the app can then map out the itineraries in full interactive details, with maps and visual in a very intuitive way so that it can give the user a summary idea of the trip in a easy to understand way.



--------------------------------------------

Build out a prototype of this application with all the interactions and build ups necessary. Assume any example for destinations, itinerary or any other details as needed.

Make the app intuitive, visual oriented but not overly cluttered with visuals or options. Stay focused on a minimalistic approach when it comes to text and labeling.  


