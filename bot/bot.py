import os
import atexit

import discord
from discord.ext import commands, tasks
from discord.ext.commands import MissingPermissions
from dotenv import load_dotenv
import json
from datetime import timedelta

from sourceserver.sourceserver import SourceServer
from sourceserver.exceptions import SourceError
from tablemaker import makeTable

# Initialise variable from local storage
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = os.getenv("COMMAND_PREFIX")
PING_COOLDOWN = os.getenv("PING_COOLDOWN")
COLOUR = int(os.getenv("COLOUR"), 16)

JSON = json.load(open(os.path.join(os.path.dirname(os.path.realpath(__file__)), "data.json"), "r"))
for channelID, connectionObj in JSON.items():
	JSON[channelID]["server"] = SourceServer(connectionObj["server"])

# Define and register clean shutdown function
def onExit(filepath: str):
	print("Performing safe shutdown")

	for channelID, connectionObj in JSON.items():
		JSON[channelID]["server"] = "%s:%d" % (connectionObj["server"]._ip, connectionObj["server"]._port)
	json.dump(JSON, open(os.path.join(os.path.dirname(os.path.realpath(filepath)), "data.json"), "w"))

atexit.register(onExit, __file__)

# Utility to convert timedelta to formatted string
def formatTimedelta(delta: timedelta) -> str:
	days, seconds = delta.days, delta.seconds
	hours = seconds // 3600
	minutes = (seconds % 3600) // 60
	seconds = seconds % 60

	datetimeStr = ""
	if days != 0: datetimeStr += "%d days " % days
	if hours != 0: datetimeStr += "%dhrs " % hours
	if minutes != 0: datetimeStr += "%dmin " % minutes
	if seconds != 0: datetimeStr += "%dsec" % seconds

	return datetimeStr

# Server admin commands (Note, these commands can be run in any channel by people who have manage server perms, even when told not to run)
class ServerCommands(commands.Cog):
	'''Server commands to be used by anyone with manager server permissions'''

	def __init__(self, bot):
		self.bot = bot
		self.pingServer.start() # PyLint sees this as an error, even though it's not
	
	@commands.command()
	@commands.has_permissions(manage_guild=True)
	async def connect(self, ctx, connectionString: str):
		'''Adds a connection to a source server to this channel'''

		if ctx.channel.id in JSON.keys():
			connection = (JSON[str(ctx.channel.id)]["server"]._ip, JSON[str(ctx.channel.id)]["server"]._port)
			await ctx.send("This channel is already connected to %s:%d, use `!removeConnection` to remove it" % connection)
			return
		
		try: JSON.update({
			str(ctx.channel.id): {"server": SourceServer(connectionString), "toNotify": []}
		})
		except SourceError as e: await ctx.send("Error, " + e.message.split(" | ")[1])
		except ValueError: await ctx.send("Connection string invalid")
		else: await ctx.send("Successfully connected to server!")
	
	@commands.command()
	@commands.has_permissions(manage_guild=True)
	async def disconnect(self, ctx):
		'''Removes this channel's connection to a source server'''

		if str(ctx.channel.id) not in JSON.keys(): await ctx.send("This channel isn't connected to a server"); return

		del JSON[str(ctx.channel.id)]
		await ctx.send("Connection removed successfully!")
	
	@commands.command()
	@commands.has_permissions(manage_guild=True)
	async def close(self, ctx):
		'''Closes the connection to the server'''
		if JSON[str(ctx.channel.id)]["server"].isClosed: await ctx.send("Server is already closed"); return

		JSON[str(ctx.channel.id)]["server"].close()
		await ctx.send("Server closed successfully!\nReconnect with `!retry`")
	
	@commands.command()
	@commands.has_permissions(manage_guild=True)
	async def retry(self, ctx):
		'''Attempts to reconnect to the server'''
		if not JSON[str(ctx.channel.id)]["server"].isClosed: await ctx.send("Server is already connected"); return

		JSON[str(ctx.channel.id)]["server"].retry()
		if JSON[str(ctx.channel.id)]["server"].isClosed: await ctx.send("Failed to reconnect to server")
		else: await ctx.send("Successfully reconnected to server!")
	
	@commands.command()
	@commands.has_permissions(manage_guild=True)
	async def notifyIfDown(self, ctx, personToNotify: discord.User, shouldNotify: bool = True):
		'''Tells the bot to change notification status for a person. If no toggle passed, defaults to true'''

		if personToNotify.bot: await ctx.send("Bots cannot be notified if the server is down"); return

		if shouldNotify:
			if ctx.message.author.id in JSON[str(ctx.channel.id)]["toNotify"]: await ctx.send(f"Already configured to notify {personToNotify.name}")
			else:
				JSON[str(ctx.channel.id)]["toNotify"].append(personToNotify.id)
				await ctx.send(f"{personToNotify.name} will now be notified if the server is down")
		else:
			if ctx.message.author.id not in JSON[str(ctx.channel.id)]["toNotify"]: await ctx.send(f"Already configured to not notify {personToNotify.name}")
			else:
				JSON[str(ctx.channel.id)]["toNotify"].remove(personToNotify.id)
				await ctx.send(f"{personToNotify.name} will no longer be notified if the server is down")

	# Cog error handler
	async def cog_command_error(self, ctx, error):
		if isinstance(error, SourceError):
			await ctx.send(f"A server error occured, see the logs for details")
			print(error.message)
		elif isinstance(error, MissingPermissions):
			await ctx.send(f"You don't have permission to run that command <@{ctx.message.author.id}>")
		elif isinstance(error, commands.errors.MissingRequiredArgument):
			await ctx.send("Command missing required argument, see `!help`")
		else: raise error
	
	# Tasks
	def cog_unload(self):
		self.pingServer.cancel() # PyLint sees this as an error, even though it's not
	
	@tasks.loop(minutes=int(PING_COOLDOWN))
	async def pingServer(self):
		await self.bot.wait_until_ready()

		for channelID, serverCon in JSON.items():
			if serverCon["server"].isClosed: return
			try: serverCon["server"].ping()
			except SourceError:
				for personToNotify in serverCon["toNotify"]:
					user = self.bot.get_user(personToNotify)
					guildName = self.bot.get_channel(int(channelID)).guild.name
					await user.send(f'''
					**WARNING:** The Source Dedicated Server @ {serverCon["server"]._ip}:{serverCon["server"]._port} assigned to this bot is down!\n*You are receiving this message as you are set to be notified if the server goes down at {guildName}*
					''')
				
				serverCon["server"].close()

# User commands
class UserCommands(commands.Cog):
	'''Commands to be run by any user in a channel with a connection'''

	@commands.command()
	async def info(self, ctx, infoName: str = None):
		'''Gets server info, all if no name specified\nSee https://github.com/100PXSquared/pythonsourceserver/wiki/SourceServer#the-info-property-values'''
		try: info = JSON[str(ctx.channel.id)]["server"].info
		except SourceError as e:
			await ctx.send("Unable to get info")
			print(e.message)
			return
		
		if infoName is None:
			embed = discord.Embed(title="Server Info", description="{name} is playing {game} on {map}".format(**info), colour=COLOUR)

			embed.add_field(name="Players", value="{players}/{max_players}".format(**info), inline=True)
			embed.add_field(name="Bots", value=str(info["bots"]), inline=True)
			embed.add_field(name=u"\u200B", value=u"\u200B", inline=True)

			embed.add_field(name="VAC", value=("yes" if info["VAC"] == 1 else "no"), inline=True)
			embed.add_field(name="Password", value=("yes" if info["visibility"] == 1 else "no"), inline=True)
			embed.add_field(name=u"\u200B", value=u"\u200B", inline=True)

			if info["game"] == "The Ship":
				embed.add_field(name="Mode", value=JSON[str(ctx.channel.id)]["server"].MODES[info["mode"]], inline=True)
				embed.add_field(name="Witnesses Needed", value=str(info["witnesses"]), inline=True)
				embed.add_field(name="Time Before Arrest", value="%d seconds" % info["duration"], inline=True)

			embed.set_footer(text="Keywords: " + info["keywords"])
			
			await ctx.send(embed=embed)
			return
		
		if infoName in ("mode", "witnesses", "duration") and info["game"] != "The Ship":
			await ctx.send("%s is only valid on servers running The Ship" % infoName)
			return
		
		if infoName not in info.keys():
			embed = discord.Embed(
				title="'%s' is invalid" % infoName,
				description="See [the wiki](https://github.com/100PXSquared/pythonsourceserver/wiki/SourceServer#the-info-property-values \"Python Source Server Query Library Wiki\")",
				colour=COLOUR
			)
			await ctx.send(embed=embed); return
		
		await ctx.send("%s is " % infoName + str(info[infoName]))
	
	@commands.command()
	async def players(self, ctx):
		'''Gets all players on the server'''

		try:
			count, plrs = JSON[str(ctx.channel.id)]["server"].getPlayers()
			isTheShip = JSON[str(ctx.channel.id)]["server"].info["game"] == "The Ship"
			srvName = JSON[str(ctx.channel.id)]["server"].info["name"]
		except SourceError as e:
			await ctx.send("Unable to get players")
			print(e.message)
			return

		if count == 0:
			await ctx.send("Doesn't look like there's anyone online at the moment, try again later")
			return
		
		embed = discord.Embed(colour=COLOUR)
		val = ""
		for player in plrs:
			if player[1] == "": continue

			val += "*%s*\n" % player[1]
			if not isTheShip:
				val += "Score: %d | Time on server: %s\n\n" % (player[2], formatTimedelta(timedelta(seconds=player[3])))
			else:
				val += "Score: %d | Deaths: %d | Money: %d\n\n" % (player[2], player[4], player[5])

		embed.add_field(name=f"Players on server {srvName}", value=val, inline=False)
		await ctx.send(embed=embed)
	
	# Command validity checks
	async def cog_check(self, ctx):
		if str(ctx.channel.id) not in JSON: return False

		if JSON[str(ctx.channel.id)]["server"].isClosed:
			await ctx.send("Server is closed, please try again later")
			return False
		
		return True

	# Cog error handler
	async def cog_command_error(self, ctx, error):
		if isinstance(error, SourceError):
			await ctx.send(f"A server error occured, see the logs for details")
			print(error.message)
		elif isinstance(error, commands.errors.CheckFailure): pass
		elif isinstance(error, commands.errors.MissingRequiredArgument):
			await ctx.send("Command missing required argument, see `!help`")
		else: raise error

bot = commands.Bot(PREFIX)
bot.add_cog(ServerCommands(bot))
bot.add_cog(UserCommands(bot))
bot.run(TOKEN)